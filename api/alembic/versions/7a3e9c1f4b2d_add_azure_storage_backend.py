"""add azure storage backend

Revision ID: 7a3e9c1f4b2d
Revises: f2e1d0c9b8a7
Create Date: 2026-09-22

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "7a3e9c1f4b2d"
down_revision: Union[str, Sequence[str], None] = "f2e1d0c9b8a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="storage_backend",
        new_values=["s3", "minio", "azure"],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="workflow_runs",
                column_name="storage_backend",
            )
        ],
        enum_values_to_rename=[],
    )
    op.sync_enum_values(
        enum_schema="public",
        enum_name="recording_storage_backend",
        new_values=["s3", "minio", "azure"],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="workflow_recordings",
                column_name="storage_backend",
            )
        ],
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="storage_backend",
        new_values=["s3", "minio"],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="workflow_runs",
                column_name="storage_backend",
            )
        ],
        enum_values_to_rename=[],
    )
    op.sync_enum_values(
        enum_schema="public",
        enum_name="recording_storage_backend",
        new_values=["s3", "minio"],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="workflow_recordings",
                column_name="storage_backend",
            )
        ],
        enum_values_to_rename=[],
    )
