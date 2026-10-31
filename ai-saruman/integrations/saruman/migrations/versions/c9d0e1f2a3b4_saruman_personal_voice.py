"""Refresh untouched stock speech, preserving edited/disabled operator records."""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def records(version):
    data = json.loads(
        (
            Path(__file__).resolve().parents[1] / f"data/action_speech_{version}.json"
        ).read_text(encoding="utf-8")
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
    for i, entry in enumerate(data["lore"]):
        rows.append(
            {
                "id": f"lore.{i + 1}",
                "kind": "authored_backstory",
                "enabled": True,
                "event": "lore." + entry["topic"]
                if isinstance(entry, dict)
                else "lore",
                "text": entry["text"] if isinstance(entry, dict) else entry,
            }
        )
    return {row["id"]: row for row in rows}


def refresh(source, target):
    table = sa.table(
        "action_voice_lines",
        sa.column("id", sa.String),
        sa.column("kind", sa.String),
        sa.column("event", sa.String),
        sa.column("text", sa.Text),
        sa.column("enabled", sa.Boolean),
    )
    db = op.get_bind()
    for key, row in target.items():
        current = (
            db.execute(sa.select(table).where(table.c.id == key)).mappings().first()
        )
        if current is None:
            db.execute(table.insert().values(**row))
        elif key in source and all(
            current[field] == source[key][field] for field in ("text", "kind", "event")
        ):
            db.execute(
                table.update()
                .where(table.c.id == key)
                .values(text=row["text"], kind=row["kind"], event=row["event"])
            )
    for key in source.keys() - target.keys():
        row = source[key]
        db.execute(
            table.delete().where(
                table.c.id == key,
                table.c.text == row["text"],
                table.c.kind == row["kind"],
                table.c.event == row["event"],
                table.c.enabled.is_(True),
            )
        )


def upgrade():
    refresh(records("v1"), records("v2"))


def downgrade():
    refresh(records("v2"), records("v1"))
