"""add azure storage backend

Revision ID: 7a3e9c1f4b2d
Revises: 00b0201ad918
Create Date: 2026-09-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "7a3e9c1f4b2d"
down_revision: Union[str, Sequence[str], None] = "00b0201ad918"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sync(enum_name: str, table: str, column: str, values: list[str]) -> None:
    # The column defaults ('s3'::<enum>) reference the old enum type and
    # block the rewrite, so drop them around the sync and restore after.
    op.alter_column(table, column, server_default=None)
    try:
        op.sync_enum_values(
            enum_schema="public",
            enum_name=enum_name,
            new_values=values,
            affected_columns=[
                TableReference(
                    table_schema="public",
                    table_name=table,
                    column_name=column,
                )
            ],
            enum_values_to_rename=[],
        )
    finally:
        op.alter_column(
            table,
            column,
            server_default=sa.text(f"'s3'::{enum_name}"),
            existing_type=sa.Enum(name=enum_name),
        )


def upgrade() -> None:
    _sync("storage_backend", "workflow_runs", "storage_backend",
          ["s3", "minio", "azure"])
    _sync(
        "recording_storage_backend",
        "workflow_recordings",
        "storage_backend",
        ["s3", "minio", "azure"],
    )


def downgrade() -> None:
    _sync("storage_backend", "workflow_runs", "storage_backend",
          ["s3", "minio"])
    _sync(
        "recording_storage_backend",
        "workflow_recordings",
        "storage_backend",
        ["s3", "minio"],
    )
