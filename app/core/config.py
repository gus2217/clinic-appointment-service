from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLINIC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/clinicdb"
    clinic_timezone: str = "Africa/Nairobi"
    slot_minutes: int = 30
    lead_time_minutes: int = 60

    # Deny by default: an unauthenticated caller must never inherit admin rights.
    auth_required: bool = True
    trusted_proxy: bool = True

    pool_size: int = 5
    max_overflow: int = 5
    statement_timeout_ms: int = 3000
    lock_timeout_ms: int = 1000

    @property
    def async_database_url(self) -> str:
        # Managed providers hand out postgres:// or postgresql:// URLs; force the
        # async psycopg 3 driver so create_async_engine never falls back to psycopg2.
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url


settings = Settings()
