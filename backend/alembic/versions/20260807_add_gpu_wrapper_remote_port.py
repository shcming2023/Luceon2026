"""freeze the managed wrapper remote-port policy

Revision ID: 20260807_gpu_wrapper_port
Revises: 20260807_gpu_runtime_settings
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision: str = "20260807_gpu_wrapper_port"
down_revision: Union[str, None] = "20260807_gpu_runtime_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("gpu_runtime_settings")}
    if "wrapper_remote_port" not in columns:
        op.add_column(
            "gpu_runtime_settings",
            sa.Column("wrapper_remote_port", sa.Integer(), nullable=False, server_default="18080"),
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("gpu_runtime_settings")}
    if "wrapper_remote_port" in columns:
        op.drop_column("gpu_runtime_settings", "wrapper_remote_port")
