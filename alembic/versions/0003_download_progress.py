"""Add nullable download progress fields.

Revision ID: 0003_download_progress
Revises: 0002_user_auth
Create Date: 2026-10-04 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003_download_progress'
down_revision: Union[str, None] = '0002_user_auth'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('downloads', sa.Column('downloaded_bytes', sa.BigInteger(), nullable=True))
    op.add_column('downloads', sa.Column('total_bytes', sa.BigInteger(), nullable=True))
    op.add_column('downloads', sa.Column('progress_percent', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('downloads', 'progress_percent')
    op.drop_column('downloads', 'total_bytes')
    op.drop_column('downloads', 'downloaded_bytes')