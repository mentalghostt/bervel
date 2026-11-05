"""Scoped discovery, complete table arithmetic and explicitly paged evidence."""
from dataclasses import replace
from io import BytesIO
import json
from uuid import UUID, uuid4

import pytest

from bervel.core.document_memory import DocumentMemory
from bervel.core.errors import CoreError
from bervel.core.models import ArtifactRef, RegisterArtifact
from bervel.modules.chat import ChatService
from bervel.modules.chat.planner import parse_plan, planner_prompt
from test_assistant_workflows import actor_with_chat, build_actions
from test_knowledge import setup


def memory_for(services, pg_engine):
    knowledge, repository, _, artifacts, _ = setup(services, pg_engine)
    return DocumentMemory(artifacts, repository, knowledge.reader, knowledge=knowledge), knowledge


def upload(services, actor, content, filename='stock-mock.csv', media='text/csv', role='stock'):
    reference = services[0].register(actor, RegisterArtifact(content, filename, media, {}, uuid4().hex))
    services[0].set_usage_role(actor, reference.artifact_id, role)
    return reference


def csv_rows(count):
    return ('sku,product,quantity,category\n' + ''.join(
        f'{number},Болт {number},0.1,{"A" if number % 2 else "B"}\n' for number in range(1, count + 1))).encode()


def action(services, pg_engine, memory, operation, arguments, *, actor=None):
    return build_actions(services, pg_engine).execute(actor or actor_with_chat(services), uuid4(), 0,
        operation, arguments, memory=memory)['data']


def test_scoped_search_discovers_content_without_filename_and_excludes_foreign_deleted_and_unrelated(services, pg_engine):
    actor = actor_with_chat(services)
    memory, knowledge = memory_for(services, pg_engine)
    # Existing generic indices remain searchable after assigning the stock role.
    wanted = upload(services, actor, csv_rows(11), role='general')
    upload(services, actor, b'Payroll unrelated', 'payroll.txt', 'text/plain', 'general')
    upload(services, replace(actor, data_scope='other'), csv_rows(1), 'foreign.csv')
    deleted = upload(services, actor, csv_rows(1), 'deleted.csv')
    while knowledge.run_once(): pass
    services[0].set_usage_role(actor, wanted.artifact_id, 'stock')
    services[0].delete(actor, deleted.artifact_id)
    result = action(services, pg_engine, memory, 'knowledge.search', {'query': 'Болт'})
    assert [item['artifact_id'] for item in result['matches']] == [str(wanted.artifact_id)]
    assert result['catalog_total'] == 2 and result['retrieval_complete'] is False
    assert result['sources'] and all(s['artifact_id'] == str(wanted.artifact_id) for s in result['sources'])
    assert result['matches'][0]['is_mock'] is True and 'mock' in result['matches'][0]['notice']
    by_role = action(services, pg_engine, memory, 'knowledge.search', {'query': 'Болт', 'usage_role': 'stock'})
    assert by_role['catalog_total'] == 1 and by_role['matches'][0]['usage_role'] == 'stock'


def test_pages_reach_last_chunk_and_query_counts_all_raw_rows_with_decimal_sums(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, csv_rows(2290))
    reference = {'artifact_id': str(original.artifact_id)}
    page = action(services, pg_engine, memory, 'file.read_page', {**reference, 'offset': 2289, 'limit': 1})
    assert page['total'] == 2290 and page['shown'] == 1 and page['next_offset'] is None
    assert page['pagination_unit'] == 'chunk' and page['truncated'] is True
    assert page['sources'][0]['row_start'] == 2290 and 'Болт 2290' in page['sources'][0]['excerpt']
    first = action(services, pg_engine, memory, 'file.read_page', {**reference, 'limit': 2})
    assert first['shown'] == 2 and first['next_offset'] == 2
    result = action(services, pg_engine, memory, 'file.query', {**reference, 'sum_columns': ['quantity'], 'group_by': 'category'})
    assert result['count'] == 2290 and result['sums'] == {'quantity': '229.0'}
    assert result['groups'] == [
        {'value': 'A', 'count': 1145, 'sums': {'quantity': '114.5'}},
        {'value': 'B', 'count': 1145, 'sums': {'quantity': '114.5'}}]
    assert result['complete'] is True and result['is_mock'] is True
    assert services[0].read(actor, original).content == csv_rows(2290)


@pytest.mark.parametrize('operation,extra', [('file.read_page', {}), ('file.query', {'sum_columns': ['quantity']})])
def test_foreign_original_cannot_be_read_paged_or_queried(services, pg_engine, operation, extra):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    foreign = upload(services, replace(actor, data_scope='other'), csv_rows(2))
    with pytest.raises(CoreError) as error:
        action(services, pg_engine, memory, operation, {'artifact_id': str(foreign.artifact_id), **extra})
    assert error.value.code == 'NOT_FOUND'


def test_oversized_csv_query_rejects_partial_counts_instead_of_summing_first_10000_rows(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, csv_rows(10001))
    with pytest.raises(CoreError) as error:
        action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id)})
    assert error.value.code == 'FILE_QUERY_INCOMPLETE'


@pytest.mark.parametrize('cell', ['unknown', '', 'NaN', 'Infinity', '1,234.56'])
def test_non_numeric_or_ambiguous_cells_never_silently_become_zero(services, pg_engine, cell):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, f'product;quantity\nБолт;{cell}\n'.encode())
    with pytest.raises(CoreError) as error:
        action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']})
    assert error.value.code == 'FILE_QUERY_NON_NUMERIC'


def workbook(*, formula=False, multiple=False):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Stock'
    sheet.append(['product', 'quantity', 'category'])
    sheet.append(['Болт', '=1+2' if formula else 1.25, 'A'])
    sheet.append(['Гайка', 2.75, 'A'])
    if multiple:
        other = book.create_sheet('Other')
        other.append(['product', 'quantity', 'category'])
        other.append(['Шайба', 1000, 'B'])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_xlsx_query_requires_sheet_for_multiple_tables_and_sums_exact_cells(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, workbook(multiple=True), 'stock-mock.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    reference = {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']}
    with pytest.raises(CoreError) as error:
        action(services, pg_engine, memory, 'file.query', reference)
    assert error.value.code == 'FILE_QUERY_SHEET_REQUIRED'
    result = action(services, pg_engine, memory, 'file.query', {**reference, 'sheet': 'Stock', 'group_by': 'category'})
    assert result['sheet'] == 'Stock' and result['count'] == 2 and result['sums'] == {'quantity': '4.00'}
    assert result['groups'] == [{'value': 'A', 'count': 2, 'sums': {'quantity': '4.00'}}]


def test_xlsx_formula_is_countable_but_cannot_be_used_as_an_exact_numeric_value(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, workbook(formula=True), 'stock.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    reference = {'artifact_id': str(original.artifact_id)}
    result = action(services, pg_engine, memory, 'file.query', reference)
    assert result['count'] == 2 and result['complete'] is True
    with pytest.raises(CoreError) as error:
        action(services, pg_engine, memory, 'file.query', {**reference, 'sum_columns': ['quantity']})
    assert error.value.code == 'FILE_QUERY_NON_NUMERIC'


def test_model_chain_search_read_exact_totals_then_create_preserves_mock_provenance_and_original(services, pg_engine):
    actor = actor_with_chat(services)
    memory, knowledge = memory_for(services, pg_engine)
    original = upload(services, actor, csv_rows(11), 'bervel-free-stock-mock.csv')
    while knowledge.run_once(): pass
    question = 'Составь PDF по нашему складу с числом SKU и общим остатком.'
    actions = build_actions(services, pg_engine)
    class Model:
        def generate(self, request_id, prompt, **options):
            value = json.loads(prompt.split('\n', 1)[1])
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            assert len(prompt.encode()) < 192 * 1024
            results = value['ACTION_RESULTS']
            if not results:
                assert value['HISTORY_UNTRUSTED'][0]['question'] == 'Мы БЕРВЕЛ, продукцию и склад можно найти в БД.'
                operation, arguments = 'knowledge.search', {'query': 'Болт', 'usage_role': 'stock'}
            elif len(results) == 1:
                matched = results[0]['data']['matches'][0]
                assert matched['artifact_id'] == str(original.artifact_id)
                operation, arguments = 'file.read_page', {'artifact_id': matched['artifact_id'], 'limit': 20}
            elif len(results) == 2:
                assert len(value['CURRENT_DOCUMENTS']) >= 11
                assert results[-1]['data']['total'] == results[-1]['data']['shown'] == 11
                operation, arguments = 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']}
            elif len(results) == 3:
                total = results[-1]['data']
                assert total['count'] == 11 and total['sums']['quantity'] == '1.1'
                assert total['is_mock'] is True and 'mock' in total['notice']
                operation, arguments = 'file.create', {'format': 'pdf', 'filename': 'stock.pdf', 'title': 'Склад (mock)',
                    'content': 'Демонстрационные данные mock. 11 SKU, общий остаток 1.1. Источник: bervel-free-stock-mock.csv, строки 1–11.'}
            else:
                return {'answer': '{"answer":"Сохранено","actions":[]}', 'model': 'fixture'}
            return {'answer': json.dumps({'answer': '', 'actions': [{'operation': operation, 'arguments': arguments,
                'authorization_quote': question}]}), 'model': 'fixture'}
    service = ChatService(None, memory, Model(), actions=actions)
    turn = {'turn_id': uuid4(), 'question': question}
    sources = []
    _, _, results = service._assistant(actor, turn, [{'state': 'completed', 'question': 'Мы БЕРВЕЛ, продукцию и склад можно найти в БД.',
        'answer': 'Понял.'}], sources, [], [])
    assert [r['operation'] for r in results[:-1]] == ['knowledge.search', 'file.read_page', 'file.query']
    assert results[-1]['kind'] == 'file'
    created = ArtifactRef(UUID(results[-1]['artifact_id']))
    assert created != original and services[0].read(actor, created).content.startswith(b'%PDF-')
    assert services[0].read(actor, original).content == csv_rows(11)
    assert all(s['artifact_id'] == str(original.artifact_id) for s in sources)


def test_data_operations_wire_nulls_defaults_and_invalid_parameters():
    for operation, arguments, expected in [
        ('knowledge.search', {'query': 'Болт', 'usage_role': None, 'limit': None}, {'query': 'Болт'}),
        ('file.read_page', {'artifact_id': str(uuid4()), 'offset': None, 'limit': None}, None),
        ('file.query', {'artifact_id': str(uuid4()), 'sum_columns': None, 'group_by': None, 'sheet': None}, None),
    ]:
        action = {'operation': operation, 'arguments': arguments, 'authorization_quote': ''}
        normalized = parse_plan(json.dumps({'answer': '', 'actions': [action]}), 'Найди продукцию')['actions'][0]['arguments']
        assert normalized == (expected or {'artifact_id': arguments['artifact_id']})
    for operation, arguments in [
        ('knowledge.search', {'query': ''}), ('file.read_page', {'artifact_id': str(uuid4()), 'limit': 101}),
        ('file.query', {'artifact_id': str(uuid4()), 'sql': 'SELECT * FROM artifacts'}),
    ]:
        with pytest.raises(CoreError) as error:
            parse_plan(json.dumps({'answer': '', 'actions': [{'operation': operation, 'arguments': arguments, 'authorization_quote': ''}]}), 'Покажи склад')
        assert error.value.code == 'ASSISTANT_INVALID_PLAN'


def test_large_file_page_projection_keeps_honest_total_and_next_offset():
    identifier = str(uuid4())
    sources = [{'artifact_id': identifier, 'filename': 'large-mock.csv', 'excerpt': 'Болт' * 500,
        'row_start': i + 1, 'row_end': i + 1, 'total_rows': 2290, 'unit': 'row'} for i in range(100)]
    result = {'kind': 'data', 'operation': 'file.read_page', 'data': {'artifact_id': identifier, 'filename': 'large-mock.csv',
        'pagination_unit': 'chunk', 'total': 2290, 'shown': 100, 'offset': 0, 'limit': 100, 'next_offset': 100,
        'truncated': True, 'sources': sources}}
    prompt = planner_prompt('Прочитай склад', [], [], [], [result])
    shown = json.loads(prompt.split('\n', 1)[1])['ACTION_RESULTS'][0]['data']
    assert shown['total'] == 2290 and isinstance(shown['sources'], list)
    assert shown['shown'] == len(shown['sources']) < 100
    assert shown['next_offset'] == shown['shown'] and shown['truncated'] is True
    assert shown['read_next']['operation'] == 'file.read_page'


def test_created_document_keeps_server_mock_notice_even_if_model_omits_it(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, csv_rows(11))
    actions, turn = build_actions(services, pg_engine), uuid4()
    actions.execute(actor, turn, 0, 'file.query', {'artifact_id': str(original.artifact_id)}, memory=memory)
    created = actions.execute(actor, turn, 1, 'file.create', {'format': 'txt', 'filename': 'report.txt',
        'title': 'Склад', 'content': '11 строк на складе'}, memory=memory)
    text = services[0].read(actor, ArtifactRef(UUID(created['artifact_id']))).content.decode()
    assert 'mock' in text and 'не фактический склад' in text
    assert '11 строк' in text


def test_query_negative_decimal_and_unknown_columns_are_explicit(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, 'sku;quantity\nA;-0,125\nB;+0,375\n'.encode())
    result = action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']})
    assert result['sums'] == {'quantity': '0.250'}
    with pytest.raises(CoreError) as error:
        action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['missing']})
    assert error.value.code == 'VALIDATION_ERROR'


def test_partial_extracted_file_does_not_become_complete_by_paging_to_end(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, csv_rows(10001))
    page = action(services, pg_engine, memory, 'file.read_page', {'artifact_id': str(original.artifact_id), 'offset': 9999, 'limit': 1})
    assert page['status'] == 'partial' and page['source_complete'] is False
    assert page['shown'] == 1 and page['next_offset'] is None and page['truncated'] is True
    assert page['sources'][0]['truncated'] is True


def test_explicit_mock_cells_survive_generic_filename_and_mark_created_csv_without_changing_its_table(services, pg_engine):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    original = upload(services, actor, b'sku,quantity,data_kind\nA,1,mock\n', 'generic.csv')
    actions, turn = build_actions(services, pg_engine), uuid4()
    result = actions.execute(actor, turn, 0, 'file.query', {'artifact_id': str(original.artifact_id)}, memory=memory)['data']
    assert result['is_mock'] is True and result['sources'][0]['is_mock'] is True
    table = 'sku,quantity\nA,1\n'
    created = actions.execute(actor, turn, 1, 'file.create', {'format': 'csv', 'filename': 'export.csv', 'title': '', 'content': table})
    assert created['is_mock'] is True and 'mock' in created['notice']
    saved = services[0].read(actor, ArtifactRef(UUID(created['artifact_id'])))
    assert saved.content.decode() == table
    assert saved.metadata['source']['origin_system'] == 'bervel-assistant-mock'
    repeated = action(services, pg_engine, memory, 'file.query', {'artifact_id': created['artifact_id']})
    assert repeated['is_mock'] is True
    read = action(services, pg_engine, memory, 'file.read', {'artifact_id': created['artifact_id']})
    assert read['sources'] and all(source['is_mock'] for source in read['sources'])


@pytest.mark.parametrize('xlsx', [False, True])
def test_empty_tables_have_zero_count_and_valid_zero_source_locators(services, pg_engine, xlsx):
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    if xlsx:
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(['sku', 'quantity'])
        output = BytesIO()
        book.save(output)
        original = upload(services, actor, output.getvalue(), 'empty.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    else:
        original = upload(services, actor, b'sku,quantity\n', 'empty.csv')
    result = action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']})
    assert result['count'] == 0 and result['sums'] == {'quantity': '0'}
    assert result['sources'][0]['row_start'] == result['sources'][0]['row_end'] == 0


def test_xlsx_table_source_preserves_blank_rows_and_actual_sheet_row_numbers(services, pg_engine):
    from openpyxl import Workbook
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Real rows'
    sheet.append(['sku', 'quantity'])
    sheet.append(['A', 1])
    sheet.append([None, None])
    sheet.append(['B', 2])
    output = BytesIO()
    book.save(output)
    original = upload(services, actor, output.getvalue(), 'stock.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    result = action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']})
    assert result['count'] == 2 and result['sums'] == {'quantity': '3'}
    source = result['sources'][0]
    assert source['row_start'] == 2 and source['row_end'] == source['total_rows'] == 4
    assert source['sheet'] == 'Real rows'


def test_page_projection_never_skips_an_unread_tail_inside_a_chunk():
    from bervel.modules.chat.context import result_projection
    identifier = str(uuid4())
    sources = [{'artifact_id': identifier, 'filename': 'large.txt', 'excerpt': 'Большой текст. ' * 150,
        'ordinal': i, 'row_start': i+1, 'row_end': i+1, 'total_rows': 100, 'unit': 'line', 'truncated': False} for i in range(10)]
    result = {'kind': 'data', 'operation': 'file.read_page', 'data': {'artifact_id': identifier, 'filename': 'large.txt',
        'pagination_unit': 'chunk', 'total': 100, 'shown': 10, 'offset': 0, 'limit': 10, 'next_offset': 10,
        'truncated': True, 'sources': sources}}
    projected = result_projection(result, 9000)['data']
    assert projected['shown'] >= 1
    assert projected['sources'] == sources[:projected['shown']]
    assert projected['next_offset'] == projected['shown']


def test_xlsx_incorrect_dimension_hint_cannot_hide_real_rows_from_exact_totals(services, pg_engine):
    from zipfile import ZipFile
    import re
    original = BytesIO(workbook())
    changed = BytesIO()
    with ZipFile(original) as archive, ZipFile(changed, 'w') as output:
        for name in archive.namelist():
            content = archive.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                content = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:C1"', content)
            output.writestr(name, content)
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    reference = upload(services, actor, changed.getvalue(), 'stock.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    result = action(services, pg_engine, memory, 'file.query', {'artifact_id': str(reference.artifact_id), 'sum_columns': ['quantity']})
    assert result['count'] == 2 and result['sums'] == {'quantity': '4.00'}


@pytest.mark.parametrize('operation', ['knowledge.search', 'file.read', 'file.read_page'])
def test_mock_cells_propagate_through_reads_and_old_cached_index_without_exact_query(services, pg_engine, operation):
    actor = actor_with_chat(services)
    memory, knowledge = memory_for(services, pg_engine)
    original = upload(services, actor, 'product,quantity,data_kind\nБолт,1,mock\n'.encode(), 'generic.csv', role='general')
    while knowledge.run_once(): pass
    services[0].set_usage_role(actor, original.artifact_id, 'stock')
    snapshot = services[0].metadata(actor, original)
    cached = memory.repository.document_index(snapshot)
    cached.pop('is_mock', None)
    memory.repository.save_document_index(snapshot, cached)
    actions, turn = build_actions(services, pg_engine), uuid4()
    arguments = {'query': 'Болт'} if operation == 'knowledge.search' else {'artifact_id': str(original.artifact_id)}
    result = actions.execute(actor, turn, 0, operation, arguments, memory=memory, question='Болт')['data']
    assert result['sources'] and all(source['is_mock'] for source in result['sources'])
    created = actions.execute(actor, turn, 1, 'file.create', {'format': 'txt', 'filename': 'report.txt', 'title': '', 'content': 'Продукция: Болт'})
    assert created['is_mock'] is True
    assert 'mock' in services[0].read(actor, ArtifactRef(UUID(created['artifact_id']))).content.decode()


def test_xlsx_trailing_empty_optional_cells_allow_count_and_other_column_sums(services, pg_engine):
    from openpyxl import Workbook
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    book = Workbook()
    sheet = book.active
    sheet.append(['sku', 'quantity', 'note'])
    sheet.append(['A', 1, None])
    sheet.append(['B', 2, 'ok'])
    output = BytesIO()
    book.save(output)
    original = upload(services, actor, output.getvalue(), 'stock.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    result = action(services, pg_engine, memory, 'file.query', {'artifact_id': str(original.artifact_id), 'sum_columns': ['quantity']})
    assert result['count'] == 2 and result['sums'] == {'quantity': '3'}


def test_xlsx_explicit_mock_cells_mark_generic_file_page_and_created_report(services, pg_engine):
    from openpyxl import Workbook
    actor = actor_with_chat(services)
    memory, _ = memory_for(services, pg_engine)
    book = Workbook()
    book.active.append(['sku', 'quantity', 'data_kind'])
    book.active.append(['A', 1, 'mock'])
    output = BytesIO()
    book.save(output)
    original = upload(services, actor, output.getvalue(), 'generic.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    page = action(services, pg_engine, memory, 'file.read_page', {'artifact_id': str(original.artifact_id)})
    assert page['is_mock'] is True and all(source['is_mock'] for source in page['sources'])
