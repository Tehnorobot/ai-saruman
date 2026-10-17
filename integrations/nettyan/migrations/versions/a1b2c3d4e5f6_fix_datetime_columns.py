"""fix datetime columns from Text to DateTime

Revision ID: a1b2c3d4e5f6
Revises: d56e4c4c260e
Create Date: 2026-03-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd56e4c4c260e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('users', 'firstreg',
        type_=sa.DateTime(timezone=True),
        postgresql_using='firstreg::timestamp with time zone',
        nullable=False
    )
    op.alter_column('users', 'last_interact',
        type_=sa.DateTime(timezone=True),
        postgresql_using='last_interact::timestamp with time zone',
        nullable=False
    )
    op.alter_column('users', 'last_answered',
        type_=sa.DateTime(timezone=True),
        postgresql_using='last_answered::timestamp with time zone',
        nullable=True
    )
    op.alter_column('users_nicknames', 'added_date',
        type_=sa.DateTime(timezone=True),
        postgresql_using='added_date::timestamp with time zone',
        nullable=False
    )
    op.alter_column('users_dialogs', 'date',
        type_=sa.DateTime(timezone=True),
        postgresql_using='date::timestamp with time zone',
        nullable=False
    )
    op.alter_column('mind_variables', 'last_changed',
        type_=sa.DateTime(timezone=True),
        postgresql_using='last_changed::timestamp with time zone',
        nullable=False,
        server_default=sa.text('now()')
    )


def downgrade() -> None:
    op.alter_column('users', 'firstreg', type_=sa.Text(), nullable=False)
    op.alter_column('users', 'last_interact', type_=sa.Text(), nullable=False)
    op.alter_column('users', 'last_answered', type_=sa.Text(), nullable=True)
    op.alter_column('users_nicknames', 'added_date', type_=sa.Text(), nullable=False)
    op.alter_column('users_dialogs', 'date', type_=sa.Text(), nullable=False)
    op.alter_column('mind_variables', 'last_changed', type_=sa.Text(), nullable=False)
