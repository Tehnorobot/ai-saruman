"""Editable action phrases and explicitly authored character prehistory."""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "action_voice_lines",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Historical migrations must not change when the live persona evolves.
    data = json.loads(
        (Path(__file__).resolve().parents[1] / "data/action_speech_v1.json").read_text(
            encoding="utf-8"
        )
    )
    rows = [
        {
            "id": f"{event}.{i + 1}",
            "kind": "phrase",
            "event": event,
            "text": value,
            "enabled": True,
        }
        for event, values in data["phrases"].items()
        for i, value in enumerate(values)
    ]
    rows += [
        {
            "id": f"lore.{i + 1}",
            "kind": "authored_backstory",
            "event": "lore",
            "text": value,
            "enabled": True,
        }
        for i, value in enumerate(data["lore"])
    ]
    op.bulk_insert(table, rows)


def downgrade():
    op.drop_table("action_voice_lines")
