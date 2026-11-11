from importlib.resources import files
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def test_targeted_presales_upgrade_preserves_radar_and_skips_inspection_branch(pg_engine):
    assert pg_engine.url.database == 'bervel_core_stage31_test'
    schema = 'presales_upgrade_' + uuid4().hex
    with pg_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(pg_engine.url.update_query_dict({'options': '-csearch_path=' + schema}), hide_parameters=True)
    config = Config()
    config.set_main_option('script_location', str(files('bervel.core').joinpath('migrations')))
    try:
        with engine.begin() as conn:
            config.attributes['connection'] = conn
            command.upgrade(config, '0027b_radar_profiles')
            conn.execute(text("INSERT INTO ai_radar_profiles (data_scope,source,data) VALUES ('migration-test','test_api',CAST(:payload AS jsonb))"),
                         {'payload': '{"keep":true}'})
            command.upgrade(config, '0029_presales_pipeline')
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == ['0029_presales_pipeline']
            assert conn.execute(text("SELECT data FROM ai_radar_profiles WHERE data_scope='migration-test'")).scalar_one() == {'keep': True}
            columns = set(conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=:schema AND table_name='chat_turns'"), {'schema': schema}).scalars())
            assert 'delivery_state' not in columns and 'followup_of' not in columns
            assert conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema AND table_name LIKE 'presales_%'"), {'schema': schema}).scalar_one() == 5
    finally:
        engine.dispose()
        with pg_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
