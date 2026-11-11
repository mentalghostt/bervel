from bervel.core.model_gateway import MODEL_FAILURE_MESSAGES


def test_rate_limit_has_specific_honest_message_without_xai_configuration():
    message = MODEL_FAILURE_MESSAGES['MODEL_RATE_LIMITED']
    assert 'лимит' in message.lower()
    assert 'повтор' in message.lower()
    assert 'Введите ключ' not in message


def test_native_web_unavailable_never_requires_a_voice_key():
    message = MODEL_FAILURE_MESSAGES['WEB_SEARCH_UNAVAILABLE']
    assert 'Codex' in message
    assert 'Введите ключ' not in message
    assert 'xAI' not in message
