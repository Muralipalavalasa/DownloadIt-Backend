from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = 'development'
    frontend_url: str = Field(
    default='http://localhost:5173',
    validation_alias='FRONTEND_URL',
)
    app_name: str = 'DownloadIt API'
    app_version: str = '1.0.0'
    database_url: str
    jwt_secret_key: SecretStr
    jwt_algorithm: str = 'HS256'
    access_token_expire_minutes: int = Field(default=30, gt=0, le=1440)
    download_allowed_hosts: str = ''
    download_file_expiration_minutes: int = Field(default=30, gt=0, le=10080)
    download_max_bytes: int = Field(default=52428800, gt=0)
    ytdlp_enabled: bool = False
    download_storage_dir: Path = Path(__file__).resolve().parents[2] / 'storage' / 'tmp'

    @property
    def allowed_download_hosts(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower().rstrip('.')
            for host in self.download_allowed_hosts.split(',')
            if host.strip()
        )

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / '.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
    )


settings = Settings()
