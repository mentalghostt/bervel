from docker_env import initialize_env


def test_inspection_secrets_are_created_once_without_replacing_existing_credentials(tmp_path):
    initialize_env(tmp_path)
    first = (tmp_path / '.env').read_text()
    assert 'BERVEL_INSPECTION_DB_PASSWORD=' in first
    assert 'BERVEL_INSPECTION_TOKEN=' in first
    initialize_env(tmp_path)
    assert (tmp_path / '.env').read_text() == first
