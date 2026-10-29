"""The planner exposes immutable files addressed only by their ID."""
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.modules.chat.planner import OPERATIONS, authorization_prompt, validate_arguments


def test_file_revise_and_history_are_not_public_operations():
    assert 'file.revise' not in OPERATIONS
    assert 'file.history' not in OPERATIONS


def test_planner_accepts_id_only_file_reads_and_rejects_revision_fields():
    reference = {'artifact_id': str(uuid4())}
    validate_arguments('file.read', reference)
    with pytest.raises(CoreError):
        validate_arguments('file.read', {**reference, 'revision': 1})


def test_file_delete_requires_only_identity_but_ticket_edit_keeps_optimistic_revision():
    validate_arguments('file.delete', {'artifact_id': str(uuid4())})
    assert 'expected_revision' in OPERATIONS['ticket.edit']['properties']


def test_document_edit_authorizer_knows_new_file_semantics_without_delete_authority():
    prompt = authorization_prompt('Исправь текст документа', 'file.create',
        {'format': 'txt', 'filename': 'changed.txt', 'title': '', 'content': 'Changed text'})
    policy, _ = prompt.split('\n', 1)
    assert 'An explicit request to edit a document authorizes file.create' in policy
    assert 'does not authorize file.delete' in policy
