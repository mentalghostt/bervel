"""Create explicitly requested, redacted inspection documents via public services."""
from collections.abc import Callable
import re

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, RegisterArtifact
from bervel.core.services import ArtifactService, authorize, validate_key
from bervel.modules.chat import Renderer, file_card


class InspectionDocuments:
    def __init__(self, artifacts: ArtifactService, renderer: Renderer, clean: Callable):
        self.artifacts, self.renderer, self.clean = artifacts, renderer, clean

    def create(self, actor: ActorContext, arguments: dict, key: str) -> dict:
        authorize(actor, 'chat:write')
        authorize(actor, 'artifact:write')
        authorize(actor, 'artifact:read')
        validate_key(key)
        if not isinstance(arguments, dict) or set(arguments) != {'format', 'filename', 'title', 'content'}:
            raise CoreError('VALIDATION_ERROR', 'Укажите формат, имя, заголовок и содержание документа.')
        format = arguments['format']
        if format not in {'pdf', 'docx', 'svg', 'txt', 'csv'}:
            raise CoreError('VALIDATION_ERROR', 'Поддерживаются PDF, DOCX, SVG, TXT и CSV.')
        if any(not isinstance(arguments[name], str) for name in ('filename', 'title', 'content')):
            raise CoreError('VALIDATION_ERROR', 'Поля документа должны быть текстом.')
        filename, title, content = (self.clean(arguments[name]) for name in ('filename', 'title', 'content'))
        if (not 1 <= len(filename) <= 255 or re.search(r'[/\\\x00-\x1f]', filename)
                or not filename.lower().endswith('.' + format) or filename.startswith('.')):
            raise CoreError('VALIDATION_ERROR', 'Укажите имя с выбранным расширением без пути и управляющих символов.')
        if not content.strip() or len(content) > 100000 or len(title) > 200:
            raise CoreError('VALIDATION_ERROR', 'Документ должен содержать текст до100000символов и заголовок до200символов.')
        try:
            rendered, media_type = self.renderer.render(format, title, content)
        except CoreError:
            raise
        except Exception:
            raise CoreError('DOCUMENT_RENDER_FAILED', 'Не удалось сформировать документ. Файл не создан.') from None
        try:
            created = self.artifacts.register(actor, RegisterArtifact(rendered, filename, media_type,
                {'origin_system': 'bervel-inspection'}, key))
            return file_card(self.artifacts.metadata(actor, created))
        except CoreError as error:
            if error.code in {'FORBIDDEN', 'VALIDATION_ERROR', 'QUOTA_EXCEEDED', 'IDEMPOTENCY_CONFLICT'}:
                raise
            raise CoreError('ACTION_OUTCOME_UNKNOWN', 'Исход сохранения документа неизвестен; повторное создание отключено.') from None
        except Exception:
            raise CoreError('ACTION_OUTCOME_UNKNOWN', 'Исход сохранения документа неизвестен; повторное создание отключено.') from None
