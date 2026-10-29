"""Bounded assistant use case: research, synthesis, effects and local verification."""
from copy import deepcopy
from io import BytesIO
import json
import time
from uuid import UUID
from zipfile import ZipFile
from xml.etree import ElementTree

from bervel.core.errors import CoreError
from bervel.core.model_gateway import MODEL_FAILURE_MESSAGES
from bervel.core.models import ArtifactRef
from bervel.modules.chat.actions import ACTION_ERRORS, RECOVERABLE_ACTION_ERRORS
from bervel.modules.chat.context import failed_receipt, with_receipt
from bervel.modules.chat.planner import AUTH_SCHEMA, PLAN_SCHEMA, MUTATIONS, authorization_prompt, parse_plan, planner_prompt, validate
from bervel.modules.chat.workflow import READS, advance, evidence_context, initial_workflow, public_workflow, remember, research_exhausted


class AssistantWorkflow:
    def __init__(self, actor, turn, history, sources, catalog, images, *, actions, memory, model, checkpoint):
        self.actor, self.turn, self.history = actor, turn, history
        self.sources, self.catalog, self.images = sources, catalog, images
        self.actions, self.memory, self.model, self.checkpoint = actions, memory, model, checkpoint
        self.state = deepcopy(turn.get('workflow') or initial_workflow())
        self.results, self.model_results, self.effects, self.rejected = [], [], set(), set()
        self.ordinal, self.calls, self.model_name = 0, 0, None
        self.blocked_web, self.limited, self.limitations = False, False, []
        self.started = self.phase_started = time.monotonic()

    def save(self, phase=None, reason=None, *, terminal=False, **changes):
        if self.state['phase'] == 'research':
            changes['elapsed_seconds'] = round(time.monotonic() - self.started, 3)
        following = advance(self.state, phase, reason, **changes)
        if following['phase'] != self.state['phase']:
            self.phase_started = time.monotonic()
        self.state = following
        self.turn['workflow'] = following
        if not terminal:
            self.checkpoint(following)

    def remaining(self):
        if self.state['phase'] == 'research':
            remaining = self.state['max_research_seconds'] - (time.monotonic() - self.started)
        else:
            budget = 120 if self.state['phase'] == 'generating' else 180
            remaining = budget - (time.monotonic() - self.phase_started)
        if remaining < 1:
            raise CoreError('MODEL_TIMEOUT', MODEL_FAILURE_MESSAGES['MODEL_TIMEOUT'])
        return min(180, remaining)

    def plan(self):
        self.save()
        generated = self.model.generate(f"{self.turn['turn_id']}:plan:{self.calls}",
            planner_prompt(self.turn['question'], self.history, self.sources, self.catalog, self.model_results,
                workflow={**public_workflow(self.state), 'goal': self.state.get('goal')}, notebook=evidence_context(self.state)),
            output_schema=PLAN_SCHEMA, timeout_seconds=self.remaining(), **({'images': self.images} if self.images else {}))
        self.calls += 1
        self.model_name = generated['model']
        return parse_plan(generated['answer'], self.turn['question'])

    def act(self, action):
        operation, arguments = action['operation'], action['arguments']
        signature = json.dumps([operation, arguments], sort_keys=True, ensure_ascii=False)
        if operation == 'web.search' and (self.blocked_web or self.state['phase'] != 'research'):
            raise CoreError('WORKFLOW_PHASE_ERROR', 'Дополнительный веб-поиск на этом этапе остановлен.')
        if operation in MUTATIONS and signature in self.effects:
            if operation == 'web.search':
                raise CoreError('WORKFLOW_NO_PROGRESS', 'Уже выполненный веб-поиск не повторялся. Собранные сведения сохранены.')
            raise CoreError('ASSISTANT_INVALID_PLAN', 'Повтор уже выполненного действия остановлен; сохранённые результаты доступны.')
        if signature in self.rejected:
            raise CoreError('WORKFLOW_NO_PROGRESS', 'Повтор отклонённых параметров остановлен.')
        try:
            target = self.actions.target_metadata(self.actor, operation, arguments)
        except CoreError as error:
            self.actions.reject(self.actor, self.turn['turn_id'], self.ordinal, operation, arguments, error.code)
            if error.code not in RECOVERABLE_ACTION_ERRORS:
                raise
            safe = CoreError(error.code, ACTION_ERRORS.get(error.code, 'Выбранный объект недоступен.'))
            self.record_failure(action, signature, safe)
            return None
        policy, payload = authorization_prompt(self.turn['question'], operation, arguments).split('\n', 1)
        authorization = self.model.generate(f"{self.turn['turn_id']}:authorize:{self.ordinal}",
            policy + '\n' + json.dumps({**json.loads(payload), 'TARGET_METADATA_UNTRUSTED': target}, ensure_ascii=False),
            output_schema=AUTH_SCHEMA, timeout_seconds=self.remaining())
        try:
            decision = json.loads(authorization['answer'])
        except ValueError:
            raise CoreError('ACTION_NOT_AUTHORIZED', 'Не удалось подтвердить действие по запросу пользователя.') from None
        validate(decision, AUTH_SCHEMA)
        if decision['decision'] != 'allow':
            self.actions.reject(self.actor, self.turn['turn_id'], self.ordinal, operation, arguments, 'ACTION_NOT_AUTHORIZED')
            raise CoreError('ACTION_NOT_AUTHORIZED', 'Нужно уточнить действие или объект. Сохранённые результаты доступны.')
        if operation == 'web.search':
            self.save(web_searches=self.state['web_searches'] + 1)
        try:
            result = self.actions.execute(self.actor, self.turn['turn_id'], self.ordinal, operation, arguments,
                memory=self.memory, model=self.bounded_model(), question=self.turn['question'])
        except CoreError as error:
            if error.code not in RECOVERABLE_ACTION_ERRORS:
                raise
            self.record_failure(action, signature, error)
            return None
        self.results.append(result)
        self.model_results.append(with_receipt(result, operation, arguments, self.ordinal))
        remember(self.state, result, operation, self.ordinal)
        if operation in {'file.read', 'file.read_page', 'file.query', 'knowledge.search'}:
            for source in result['data']['sources']:
                evidence = {key: value for key, value in source.items() if key != 'image_attached'}
                if not any(evidence == {key: value for key, value in saved.items() if key != 'image_attached'} for saved in self.sources):
                    self.sources.append(source)
            self.images = self.memory.images(self.actor, self.sources)
        self.ordinal += 1
        if operation in MUTATIONS:
            self.effects.add(signature)
        self.save()
        return result

    def bounded_model(self):
        owner = self
        class BoundedModel:
            def generate(self, request_id, prompt, **options):
                return owner.model.generate(request_id, prompt, **{**options, 'timeout_seconds': owner.remaining()})
        return BoundedModel()

    def record_failure(self, action, signature, error):
        self.model_results.append(failed_receipt(action['operation'], action['arguments'], self.ordinal, error))
        self.rejected.add(signature)
        self.ordinal += 1
        if action['operation'] == 'web.search':
            self.blocked_web = True
        self.limitations.append(error.message)
        self.save()

    def result_text(self):
        labels = {'file': 'Файл сохранён и проверен; доступен в карточке.', 'ticket': 'Обращение сохранено.',
            'radar_run': 'Запуск радара сохранён.', 'web': 'Полученные веб-источники доступны в карточке.', 'data': 'Данные прочитаны.'}
        return ' '.join(dict.fromkeys(labels[result['kind']] for result in self.results))

    def finish(self, outcome, answer, reason=None):
        self.save(outcome, reason, terminal=True)
        return answer, self.model_name, self.results

    def run(self):
        first = self.plan()
        goal = first.get('task')
        self.typed_goal = goal is not None
        if not goal:
            drafts = [item for item in first['actions'] if item['operation'] == 'file.create']
            goal = {'deliverable': 'file' if drafts else 'operation',
                'formats': list(dict.fromkeys(item['arguments']['format'] for item in drafts)), 'research_required': False}
        self.state['goal'] = goal
        self.save()
        if goal['deliverable'] == 'operation':
            return self.operations(first)
        plan = first
        for _ in range(self.state['max_research_rounds']):
            self.save(research_rounds=self.state['research_rounds'] + 1)
            if any(item['operation'] == 'file.create' for item in plan['actions']):
                return self.synthesize(plan)
            if not plan['actions'] or plan.get('research_complete'):
                return self.synthesize(plan if goal['deliverable'] == 'answer' else None)
            for proposed in plan['actions']:
                if proposed['operation'] not in READS | {'web.search'}:
                    raise CoreError('WORKFLOW_PHASE_ERROR', 'На этапе исследования разрешены только чтение и поиск.')
                if proposed['operation'] == 'web.search' and self.blocked_web:
                    break
                try:
                    self.act(proposed)
                except CoreError as error:
                    if error.code not in MODEL_FAILURE_MESSAGES and error.code != 'WORKFLOW_NO_PROGRESS':
                        raise
                    # Authorization is a model read, not an unknown business effect.
                    self.limitations.append(MODEL_FAILURE_MESSAGES.get(error.code, error.message))
                    self.limited = True
                    break
                if research_exhausted(self.state):
                    break
            if research_exhausted(self.state) or self.blocked_web or self.limited:
                self.limited = True
                break
            try:
                plan = self.plan()
            except CoreError as error:
                if error.code not in MODEL_FAILURE_MESSAGES:
                    raise
                self.limitations.append(MODEL_FAILURE_MESSAGES[error.code])
                self.limited = True
                break
        else:
            self.limited = True
        return self.synthesize()

    def synthesize(self, prepared=None):
        goal = self.state['goal']
        if goal['research_required'] and not self.state['evidence']['entries']:
            return self.finish('blocked', 'Недостаточно подтверждённых данных для запрошенного результата. Документ не создан.',
                'Исследование не дало доступных содержательных источников.')
        reason = 'Бюджет исследования завершён; непроверенные сведения будут обозначены.' if self.limited else None
        self.save('synthesizing', reason)
        for attempt in range(2):
            plan = prepared if prepared is not None else self.plan()
            prepared = None
            self.limitations.extend(plan.get('limitations') or [])
            drafts = [item for item in plan['actions'] if item['operation'] == 'file.create']
            if goal['deliverable'] == 'answer' and not plan['actions']:
                outcome = 'partial' if self.limited or self.limitations else 'completed'
                self.save('verifying')
                return self.finish(outcome, plan['answer'], 'Ответ содержит ограничения.' if outcome == 'partial' else None)
            if not drafts or len(drafts) != len(plan['actions']):
                self.save(reason='Исследование завершено. Подготовьте запрошенный документ из сохранённых данных без новых поисков.')
                continue
            if goal['formats'] and any(item['arguments']['format'] not in goal['formats'] for item in drafts):
                self.save(reason='Формат черновика не соответствует запрошенному документу.')
                continue
            self.state['draft'] = drafts
            self.save('generating')
            for item in drafts:
                self.act(item)
            files = [item for item in self.results if item['kind'] == 'file']
            if not files:
                # A known rejection can be corrected without going back to research.
                if attempt == 0:
                    correction = self.plan()
                    corrected = [item for item in correction['actions'] if item['operation'] == 'file.create']
                    formats_match = not goal['formats'] or all(item['arguments']['format'] in goal['formats'] for item in corrected)
                    if corrected and len(corrected) == len(correction['actions']) and formats_match:
                        for item in corrected:
                            self.act(item)
                        files = [item for item in self.results if item['kind'] == 'file']
                if not files:
                    return self.finish('blocked', 'Не удалось создать документ. ' + ' '.join(dict.fromkeys(self.limitations)), 'Документ не создан.')
            self.save('verifying')
            for card in files:
                self.verify_file(card)
            formats = {card['filename'].rsplit('.', 1)[-1].lower() for card in files}
            missing = set(goal['formats']) - formats
            if missing:
                self.limitations.append('Не созданы запрошенные форматы: ' + ', '.join(sorted(missing)) + '.')
            # A corrected filename rejection no longer makes a verified document incomplete.
            failed_creates = [entry for entry in self.model_results if entry.get('_receipt', {}).get('operation') == 'file.create'
                and entry.get('error', {}).get('code') == 'VALIDATION_ERROR']
            for failed in failed_creates:
                self.limitations = [item for item in self.limitations if item != failed['error']['message']]
            outcome = 'partial' if self.limited or self.limitations else 'completed'
            note = ('Результат сохранён с ограничениями. ' + ' '.join(dict.fromkeys(self.limitations))) if outcome == 'partial' else None
            return self.finish(outcome, self.result_text() + (' ' + note if note else ''), note)
        return self.finish('blocked', 'Не удалось подготовить черновик запрошенного документа. Собранные источники сохранены.', 'Модель не подготовила допустимый документ.')

    def verify_file(self, card):
        original = self.actions.artifacts.read(self.actor, ArtifactRef(UUID(card['artifact_id'])))
        content, media = original.content, original.metadata['media_type']
        if media != card['media_type'] or not content:
            raise CoreError('WORKFLOW_VERIFICATION_FAILED', 'Сохранённый файл не прошёл проверку содержимого.')
        if media == 'application/pdf':
            if not content.startswith(b'%PDF-'):
                raise CoreError('WORKFLOW_VERIFICATION_FAILED', 'Сохранённый PDF повреждён.')
            page = self.memory.read_page(self.actor, original.ref, limit=1)
            if page['status'] != 'ready' or not any(source['excerpt'].strip() for source in page['sources']):
                raise CoreError('WORKFLOW_VERIFICATION_FAILED', 'Не удалось прочитать содержимое сохранённого PDF.')
        elif media == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            with ZipFile(BytesIO(content)) as archive:
                ElementTree.fromstring(archive.read('word/document.xml'))
        elif media == 'image/svg+xml':
            ElementTree.fromstring(content)
        elif media.startswith('text/'):
            if not content.decode('utf-8').strip():
                raise CoreError('WORKFLOW_VERIFICATION_FAILED', 'Сохранённый текст пуст.')
        self.state.setdefault('verified_files', []).append(card['artifact_id'])
        self.save()

    def operations(self, first):
        """Ordinary effects retain their typed receipts; document creation uses the file pipeline."""
        plan = first
        for step in range(6):
            if self.state['phase'] == 'research':
                self.save(research_rounds=self.state['research_rounds'] + 1)
            if not plan['actions']:
                if self.state['phase'] == 'research':
                    self.save('synthesizing')
                if self.state['phase'] != 'verifying':
                    self.save('verifying')
                useful = any(item['kind'] != 'data' or item.get('data', {}).get('sources') for item in self.results)
                outcome = ('partial' if useful else 'blocked') if self.limitations else 'completed'
                answer = self.result_text() if self.effects or self.limitations else plan['answer']
                return self.finish(outcome, answer + (' ' + ' '.join(dict.fromkeys(self.limitations)) if self.limitations else ''),
                    'Часть действий не завершена.' if self.limitations else None)
            for item in plan['actions']:
                operation = item['operation']
                if operation == 'file.create':
                    self.state['goal'] = {'deliverable': 'file', 'formats': [item['arguments']['format']], 'research_required': False}
                    return self.synthesize({'actions': [item], 'answer': ''})
                if operation in MUTATIONS and operation != 'web.search' and self.state['phase'] == 'research':
                    self.save('generating')
                if operation == 'web.search' and (self.blocked_web or self.state['web_searches'] >= self.state['max_web_searches']):
                    return self.finish('partial' if self.results else 'blocked', self.result_text() +
                        ' Дополнительный поиск остановлен. Собранные данные сохранены.', 'Лимит поиска или предыдущий отказ.')
                self.act(item)
                if self.results and self.results[-1]['kind'] == 'radar_run' and operation == 'radar.analyse':
                    self.save('verifying')
                    return self.finish('completed', self.result_text())
            if self.state['phase'] == 'research' and research_exhausted(self.state):
                if self.typed_goal:
                    raise CoreError('ASSISTANT_STEP_LIMIT', 'Бюджет исследования завершён. Сохранённые результаты доступны.')
                # Older structured clients had no goal field. Reserve one answer-only synthesis,
                # without allowing a fifth research operation or another paid search.
                self.save('synthesizing', 'Исследование завершено; формируется ответ из сохранённых данных.')
                plan = self.plan()
                if plan['actions']:
                    raise CoreError('ASSISTANT_STEP_LIMIT', 'Исследование завершено; дополнительные действия остановлены.')
                continue
            plan = self.plan() if step < 5 else plan
        raise CoreError('ASSISTANT_STEP_LIMIT', 'Достигнут лимит шагов. Сохранённые результаты доступны.')
