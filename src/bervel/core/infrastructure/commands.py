"""Shared transactional command receipts. The caller owns the SQL transaction."""
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4
from sqlalchemy import and_, insert, select, text
from sqlalchemy.engine import Connection
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.infrastructure.database import audit, command_receipts

def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def lock(connection: Connection, key: str) -> None:
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})


class CommandReceipts:
    def _receipt_filter(self, actor: ActorContext, operation: str, key: str):
        return and_(command_receipts.c.data_scope == actor.data_scope, command_receipts.c.actor_id == actor.actor_id,
                    command_receipts.c.operation == operation, command_receipts.c.command_key == key)

    def read_result(self, connection: Connection, actor: ActorContext, operation: str, key: str) -> dict | None:
        return connection.execute(select(command_receipts.c.result).where(
            self._receipt_filter(actor, operation, key))).scalar_one_or_none()

    def repeat(self, connection: Connection, actor: ActorContext, operation: str, key: str, fingerprint: str) -> dict | None:
        lock(connection, canonical_json([actor.data_scope, actor.actor_id, operation, key]))
        row = connection.execute(select(command_receipts).where(self._receipt_filter(actor, operation, key))).mappings().first()
        if row:
            if row['fingerprint'] != fingerprint:
                raise CoreError('IDEMPOTENCY_CONFLICT', 'Command key already used with different input')
            return row['result']
        return None

    def finish(self, connection: Connection, actor: ActorContext, operation: str, key: str, fingerprint: str, result: dict, *, audited: bool = True) -> None:
        if audited:
            connection.execute(insert(audit).values(audit_id=uuid4(), data_scope=actor.data_scope, actor_id=actor.actor_id,
                operation=operation, object_ref=result, created_at=datetime.now(timezone.utc)))
        connection.execute(insert(command_receipts).values(data_scope=actor.data_scope, actor_id=actor.actor_id,
            operation=operation, command_key=key, fingerprint=fingerprint, result=result))
