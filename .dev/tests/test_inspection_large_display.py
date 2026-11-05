"""Display filtering must not stall or truncate a direct native CLI conversation."""
import io
import json
import subprocess
import sys

import pytest

from bervel.core.errors import CoreError
from bervel.modules.inspection.infrastructure.client import InspectionClient
from bervel.modules.inspection.redaction import SecretRedactor


def test_large_unbroken_native_output_is_filtered_without_quadratic_scan():
    code = "from bervel.modules.inspection.redaction import SecretRedactor; value='A'*250000; assert SecretRedactor().clean(value)==value"
    subprocess.run([sys.executable, '-c', code], timeout=5, check=True)


@pytest.mark.parametrize('value', [
    'postgresql+psycopg://reader:p%2Fword@database:5432/app',
    '123http://name:private-pass@host',
    'a' * 300 + '://name:private-pass@host',
])
def test_url_passwords_remain_hidden_with_long_or_embedded_scheme(value):
    cleaned = SecretRedactor().clean(value)
    assert 'private-pass' not in cleaned and 'p%2Fword' not in cleaned
    assert '[СКРЫТО]' in cleaned and '@' in cleaned


def test_display_redaction_roundtrips_one_large_field_without_cutting(monkeypatch):
    value = 'A' * (5 * 1024 * 1024)
    raw = json.dumps({'value': value}).encode()
    monkeypatch.setattr('bervel.modules.inspection.infrastructure.client.urlopen',
        lambda *args, **kwargs: io.BytesIO(raw))
    cleaned = InspectionClient('http://reader:8091', 'x' * 32).redact(value)
    assert len(cleaned) == len(value)


def test_normal_read_page_transport_keeps_its_explicit_size_rejection(monkeypatch):
    raw = b'x' * (4 * 1024 * 1024 + 2)
    monkeypatch.setattr('bervel.modules.inspection.infrastructure.client.urlopen',
        lambda *args, **kwargs: io.BytesIO(raw))
    with pytest.raises(CoreError) as failure:
        InspectionClient('http://reader:8091', 'x' * 32)._request('/read', {})
    assert failure.value.code == 'QUOTA_EXCEEDED'
