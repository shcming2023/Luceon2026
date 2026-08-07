from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.models.base import Base
from app.models.enums import DEFAULT_MINERU_BACKEND, normalize_backend_value

class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    ocr_lang = Column(String(32), default='ch')  # lang背后对应的是ocr模型的选择
    force_ocr = Column(Boolean, default=False)
    table_recognition = Column(Boolean, default=False)
    formula_recognition = Column(Boolean, default=False)
    backend = Column(String(64), default=DEFAULT_MINERU_BACKEND)

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'ocr_lang': self.ocr_lang,
            'force_ocr': self.force_ocr,
            'table_recognition': self.table_recognition,
            'formula_recognition': self.formula_recognition,
            'backend': normalize_backend_value(self.backend)
        }


class GpuRuntimeSetting(Base):
    """Singleton versioned non-secret GPU automation policy."""
    __tablename__ = "gpu_runtime_settings"
    id = Column(Integer, primary_key=True, default=1)
    schema_version = Column(String(64), nullable=False, default="luceon.gpu-runtime-setting/v2")
    version = Column(Integer, nullable=False, default=1)
    automatic_enabled = Column(Boolean, nullable=False, default=False)
    auto_stop = Column(Boolean, nullable=False, default=True)
    take_over_running = Column(Boolean, nullable=False, default=False)
    credential_provider = Column(String(48), nullable=False, default="project_secret_file")
    endpoint = Column(String(255), nullable=False, default="https://api.compshare.cn")
    region = Column(String(64), nullable=False, default="")
    zone = Column(String(64), nullable=False, default="")
    project_id = Column(String(128), nullable=False, default="")
    uhost_id = Column(String(128), nullable=False, default="")
    ssh_host = Column(String(255), nullable=False, default="")
    ssh_port = Column(Integer, nullable=False, default=22)
    wrapper_remote_port = Column(Integer, nullable=False, default=18080)
    budget_micro_cny = Column(Integer, nullable=False, default=20_000_000)
    min_free_disk_bytes = Column(Integer, nullable=False, default=12 * 1024**3)
    disk_reserve_bytes = Column(Integer, nullable=False, default=2 * 1024**3)
    expansion_factor = Column(Integer, nullable=False, default=12)
    stop_grace_seconds = Column(Integer, nullable=False, default=60)
    updated_by_user_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
