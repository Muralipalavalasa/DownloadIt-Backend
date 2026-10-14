"""Add authentication fields and normalized email uniqueness.

Revision ID: 0002_user_auth
Revises: 0001_initial
Create Date: 2026-10-04 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002_user_auth'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column(
        'users',
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.execute(sa.text('UPDATE users SET email = lower(btrim(email))'))
    op.create_index(
        'uq_users_email_normalized',
        'users',
        [sa.text('lower(email)')],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_users_email_normalized', table_name='users')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'password_hash')