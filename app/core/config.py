from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLINIC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/clinicdb"
    clinic_timezone: str = "Africa/Nairobi"
    slot_minutes: int = 30
    lead_time_minutes: int = 60

    auth_required: bool = False

    # Directory services own doctor/patient data. local = seeded demo fixtures.
    directory_mode: Literal["local", "http", "fixture"] = "local"
    doctors_url: str = ""
    patients_url: str = ""
    directory_timeout_seconds: float = 5.0
    trusted_proxy: bool = True

    pool_size: int = 5
    max_overflow: int = 5
    statement_timeout_ms: int = 3000
    lock_timeout_ms: int = 1000

    @property
    def async_database_url(self) -> str:
        # SQLAlchemy's psycopg dialect supports async; no asyncpg needed.
        return self.database_url

settings = Settings()
