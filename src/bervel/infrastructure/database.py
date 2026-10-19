from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import (MetaData, Table, Column, String, Integer, ForeignKey, ForeignKeyConstraint,
                        CheckConstraint, DateTime, create_engine)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Engine

metadata = MetaData()
artifacts = Table('artifacts', metadata,
    Column('artifact_id', UUID(as_uuid=True), primary_key=True),
    Column('data_scope', String(128), nullable=False),
    Column('owner_module_id', String(128), nullable=False),
    Column('current_revision', Integer, nullable=False), CheckConstraint('current_revision > 0'))
artifact_revisions = Table('artifact_revisions', metadata,
    Column('artifact_id', UUID(as_uuid=True), ForeignKey('artifacts.artifact_id'), primary_key=True),
    Column('revision', Integer, primary_key=True),
    Column('metadata', JSONB, nullable=False), CheckConstraint('revision > 0'))
schema_definitions = Table('schema_definitions', metadata,
    Column('schema_id', String(128), primary_key=True), Column('schema_version', String(32), primary_key=True),
    Column('checksum', String(64), nullable=False), Column('definition', JSONB, nullable=False))
command_receipts = Table('command_receipts', metadata,
    Column('data_scope', String(128), primary_key=True), Column('actor_id', String(128), primary_key=True),
    Column('operation', String(128), primary_key=True), Column('command_key', String(128), primary_key=True),
    Column('fingerprint', String(64), nullable=False), Column('result', JSONB, nullable=False))
audit = Table('audit', metadata,
    Column('audit_id', UUID(as_uuid=True), primary_key=True), Column('data_scope', String(128), nullable=False),
    Column('actor_id', String(128), nullable=False), Column('operation', String(128), nullable=False),
    Column('object_ref', JSONB, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
datasets = Table('datasets', metadata,
    Column('dataset_id', UUID(as_uuid=True), primary_key=True),
    Column('artifact_id', UUID(as_uuid=True), ForeignKey('artifacts.artifact_id'), nullable=False),
    Column('data_scope', String(128), nullable=False), Column('owner_module_id', String(128), nullable=False),
    Column('current_revision', Integer, nullable=False), CheckConstraint('current_revision > 0'))
input_snapshots = Table('input_snapshots', metadata,
    Column('input_snapshot_id', UUID(as_uuid=True), primary_key=True),
    Column('data_scope', String(128), nullable=False), Column('inputs', JSONB, nullable=False))
process_runs = Table('process_runs', metadata,
    Column('process_run_id', UUID(as_uuid=True), primary_key=True),
    Column('data_scope', String(128), nullable=False),
    Column('input_snapshot_id', UUID(as_uuid=True), ForeignKey('input_snapshots.input_snapshot_id'), nullable=False),
    Column('producer', JSONB, nullable=False), Column('configuration_revision', Integer, nullable=False),
    Column('state', String(32), nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
dataset_revisions = Table('dataset_revisions', metadata,
    Column('dataset_id', UUID(as_uuid=True), ForeignKey('datasets.dataset_id'), primary_key=True),
    Column('revision', Integer, primary_key=True), Column('artifact_id', UUID(as_uuid=True), nullable=False),
    Column('artifact_revision', Integer, nullable=False),
    Column('input_snapshot_id', UUID(as_uuid=True), ForeignKey('input_snapshots.input_snapshot_id'), nullable=False),
    Column('process_run_id', UUID(as_uuid=True), ForeignKey('process_runs.process_run_id'), nullable=False),
    Column('schema_id', String(128), nullable=False), Column('schema_version', String(32), nullable=False),
    Column('envelope', JSONB, nullable=False), Column('record_count', Integer, nullable=False),
    ForeignKeyConstraint(['artifact_id', 'artifact_revision'], ['artifact_revisions.artifact_id', 'artifact_revisions.revision']),
    ForeignKeyConstraint(['schema_id', 'schema_version'], ['schema_definitions.schema_id', 'schema_definitions.schema_version']),
    CheckConstraint('revision > 0'), CheckConstraint('record_count > 0'))
data_records = Table('data_records', metadata,
    Column('dataset_id', UUID(as_uuid=True), primary_key=True), Column('revision', Integer, primary_key=True),
    Column('ordinal', Integer, primary_key=True), Column('record_id', String(128), nullable=False),
    Column('record', JSONB, nullable=False),
    ForeignKeyConstraint(['dataset_id', 'revision'], ['dataset_revisions.dataset_id', 'dataset_revisions.revision']))

process_jobs = Table('process_jobs', metadata,
    Column('process_run_id', UUID(as_uuid=True), ForeignKey('process_runs.process_run_id'), primary_key=True),
    Column('actor_id', String(128), nullable=False), Column('actor_permissions', JSONB, nullable=False),
    Column('command', JSONB, nullable=False), Column('workflow', JSONB, nullable=False),
    Column('invocation_hash', String(64), nullable=False), Column('active_attempt', Integer, nullable=False),
    Column('result', JSONB), Column('updated_at', DateTime(timezone=True), nullable=False),
    CheckConstraint('active_attempt > 0'))
process_attempts = Table('process_attempts', metadata,
    Column('process_run_id', UUID(as_uuid=True), ForeignKey('process_jobs.process_run_id'), primary_key=True),
    Column('number', Integer, primary_key=True), Column('execution_id', String(128), unique=True),
    Column('parent_execution_id', String(128)), Column('state', String(32), nullable=False),
    Column('error_code', String(64)), Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False), CheckConstraint('number > 0'))


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if engine.dialect.name != 'postgresql':
        engine.dispose()
        raise ValueError('PostgreSQL is required')
    return engine


def migrate(engine: Engine) -> None:
    config = Config()
    config.set_main_option('script_location', str(files('bervel').joinpath('migrations')))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
