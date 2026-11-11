"""Remove the retired radar's own tables and its references in shared history."""
from alembic import op


revision = '0024_retire_legacy_radar'
down_revision = '0023_ai_tender_radar_archive'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM chat_actions WHERE operation LIKE 'radar.%'")
    op.execute("DELETE FROM command_receipts WHERE operation LIKE 'radar.%'")
    op.execute("DELETE FROM audit WHERE operation LIKE 'radar.%'")
    op.execute("""
        UPDATE chat_turns SET results = (
            SELECT coalesce(jsonb_agg(entry ORDER BY ordinal), '[]'::jsonb)
            FROM jsonb_array_elements(results) WITH ORDINALITY AS entries(entry, ordinal)
            WHERE coalesce(entry->>'kind', '') <> 'radar_run'
                AND coalesce(entry->>'operation', '') NOT LIKE 'radar.%'
        )
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements(results) AS entry
            WHERE entry->>'kind' = 'radar_run' OR entry->>'operation' LIKE 'radar.%'
        )
    """)
    op.drop_table('radar_runs')
    op.drop_table('radar_batches')


def downgrade():
    raise RuntimeError('The retired legacy radar and its deleted history cannot be restored by downgrade')
