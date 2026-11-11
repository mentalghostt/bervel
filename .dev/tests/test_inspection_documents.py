from uuid import UUID, uuid4
import subprocess

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.modules.chat.infrastructure.documents import SafeDocumentRenderer


def document_service(services, renderer=None):
    from bervel.modules.inspection.documents import InspectionDocuments
    return InspectionDocuments(services[0], renderer or SafeDocumentRenderer(),
        lambda value: value.replace('private-value-987', '[СКРЫТО]') if isinstance(value, str) else value)


def actor(services):
    current = services[3]
    return ActorContext(current.actor_id, current.data_scope,
        current.permissions | {'chat:read', 'chat:write'})


def test_inspection_documents_are_real_originals_and_same_command_replays(services):
    service, context = document_service(services), actor(services)
    for format in ('pdf', 'docx', 'svg', 'txt', 'csv'):
        args = dict(format=format, filename='Проверка.' + format, title='Схема системы',
            content='Интерфейс\nAPI\nPostgreSQL')
        key = 'inspection-doc:' + str(uuid4())
        first = service.create(context, args, key)
        again = service.create(context, args, key)
        assert first == again and first['kind'] == 'file'
        saved = services[0].read(context, ArtifactRef(UUID(first['artifact_id'])))
        assert saved.content and first['download_url'].endswith('/content')
        assert saved.metadata['source']['origin_system'] == 'bervel-inspection'
        if format == 'svg':
            from defusedxml.ElementTree import fromstring
            root = fromstring(saved.content)
            labels = [item.text for item in root.findall('{http://www.w3.org/2000/svg}text')]
            assert labels == ['Схема системы', 'Интерфейс', 'API', 'PostgreSQL']
            assert len(root.findall('{http://www.w3.org/2000/svg}path')) == 2


def test_inspection_document_redaction_precedes_render_and_storage(services):
    context = actor(services)
    result = document_service(services).create(context,
        dict(format='txt', filename='Диагностика.txt', title='Проверка', content='token=private-value-987'), 'redacted')
    original = services[0].read(context, ArtifactRef(UUID(result['artifact_id'])))
    assert original.content.decode() == 'token=[СКРЫТО]'


@pytest.mark.parametrize('filename,format', [('../escape.pdf', 'pdf'), ('dir\\escape.pdf', 'pdf'),
    ('file.txt', 'pdf'), ('file.html', 'html'), ('nul\x00.pdf', 'pdf')])
def test_inspection_document_rejects_path_and_format_before_creating(services, filename, format):
    context = actor(services)
    before = services[0].list(context)['artifacts']
    with pytest.raises(CoreError) as failure:
        document_service(services).create(context,
            dict(format=format, filename=filename, title='Проверка', content='text'), 'invalid')
    assert failure.value.code == 'VALIDATION_ERROR'
    assert services[0].list(context)['artifacts'] == before


def test_failed_renderer_never_registers_an_artifact_or_claims_success(services):
    class FailedRenderer:
        def render(self, *args):
            raise RuntimeError('secret transport failure')
    context = actor(services)
    with pytest.raises(CoreError) as failure:
        document_service(services, FailedRenderer()).create(context,
            dict(format='pdf', filename='Отчёт.pdf', title='Отчёт', content='Факт'), 'failed')
    assert failure.value.code == 'DOCUMENT_RENDER_FAILED'
    assert 'secret' not in failure.value.message
    assert services[0].list(context)['artifacts'] == []


def test_pdf_formats_headings_emphasis_and_tables_without_executing_markup():
    raw, mime = SafeDocumentRenderer().render('pdf', 'Архитектура',
        '# Состав системы\n**Надёжное** хранение\n\n| Компонент | Назначение |\n'
        '| --- | --- |\n| API | Обработка |\n| БД | Хранение |\n\n- Первый шаг\n'
        '<img src="file:///etc/passwd"/>')
    assert mime == 'application/pdf'
    text = subprocess.run(['pdftotext', '-', '-'], input=raw, stdout=subprocess.PIPE, check=True).stdout.decode()
    assert 'Состав системы' in text and '# Состав' not in text
    assert 'Надёжное' in text and '**Надёжное**' not in text
    assert 'Компонент' in text and 'Назначение' in text and 'Хранение' in text
    assert '| --- |' not in text and 'root:x:' not in text
