"""add versioned non-secret GPU runtime settings

Revision ID: 20260807_gpu_runtime_settings
Revises: 20260715_runtime_control_plane
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260807_gpu_runtime_settings"
down_revision: Union[str, None] = "20260715_runtime_control_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    if "gpu_runtime_settings" in set(inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "gpu_runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("automatic_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_stop", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("take_over_running", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("credential_provider", sa.String(48), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("zone", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("uhost_id", sa.String(128), nullable=False),
        sa.Column("ssh_host", sa.String(255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("budget_micro_cny", sa.Integer(), nullable=False),
        sa.Column("min_free_disk_bytes", sa.Integer(), nullable=False),
        sa.Column("disk_reserve_bytes", sa.Integer(), nullable=False),
        sa.Column("expansion_factor", sa.Integer(), nullable=False),
        sa.Column("stop_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(sa.text("INSERT INTO gpu_runtime_settings (id,schema_version,version,automatic_enabled,auto_stop,take_over_running,credential_provider,endpoint,region,zone,project_id,uhost_id,ssh_host,ssh_port,budget_micro_cny,min_free_disk_bytes,disk_reserve_bytes,expansion_factor,stop_grace_seconds) VALUES (1,'luceon.gpu-runtime-setting/v2',1,0,1,0,'project_secret_file','https://api.compshare.cn','','','','','',22,20000000,12884901888,2147483648,12,60)"))

def downgrade() -> None:
    if "gpu_runtime_settings" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("gpu_runtime_settings")
