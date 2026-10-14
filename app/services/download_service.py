from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.download import Download
from app.models.user import User
from app.providers.errors import AuthorizationRequired
from app.providers.registry import ProviderRegistry, default_provider_registry
from app.services.storage_service import LocalTemporaryStorage, StoragePathError

GENERIC_DOWNLOAD_ERROR = "We couldn't prepare this download. Please try again."


def create_download_job(
    db: Session,
    user: User,
    source_url: str,
    output_format: str,
    quality: str,
    authorized_to_download: bool = False,
    registry: ProviderRegistry | None = None,
) -> Download:
    selected_registry = registry or default_provider_registry
    provider = selected_registry.get_provider(source_url)
    provider.validate_download_options(output_format, quality)
    if provider.requires_authorization and not authorized_to_download:
        raise AuthorizationRequired()

    download = Download(
        user_id=user.id,
        source_url=source_url,
        platform=provider.platform_for(source_url).value,
        status='pending',
        format=output_format,
        quality=quality,
    )
    db.add(download)
    db.commit()
    db.refresh(download)
    return download

def get_download_for_user(db: Session, download_id: UUID, user_id: UUID) -> Download:
    download = db.scalar(
        select(Download).where(Download.id == download_id, Download.user_id == user_id)
    )
    if download is None:
        raise HTTPException(status_code=404, detail='Download not found.')
    return download


def mark_processing(db: Session, download: Download) -> None:
    download.status = 'processing'
    download.error_message = None
    download.downloaded_bytes = None
    download.total_bytes = None
    download.progress_percent = None
    db.commit()


def mark_completed(
    db: Session,
    download: Download,
    title: str | None,
    file_path: str,
    file_size: int,
    now: datetime | None = None,
) -> None:
    completed_at = now or datetime.now(timezone.utc)
    download.title = title
    download.status = 'completed'
    download.file_path = file_path
    download.completed_at = completed_at
    download.expires_at = completed_at + timedelta(minutes=settings.download_file_expiration_minutes)
    download.error_message = None
    download.downloaded_bytes = file_size
    download.total_bytes = file_size
    download.progress_percent = 100
    db.commit()


def update_download_progress(
    db: Session,
    download: Download,
    downloaded_bytes: int,
    total_bytes: int | None,
) -> None:
    download.downloaded_bytes = downloaded_bytes
    download.total_bytes = total_bytes
    download.progress_percent = (
        min(100, int(downloaded_bytes * 100 / total_bytes))
        if total_bytes is not None and total_bytes > 0
        else None
    )
    db.commit()


def mark_failed(db: Session, download: Download, error_message: str = GENERIC_DOWNLOAD_ERROR) -> None:
    download.status = 'failed'
    download.error_message = error_message
    download.file_path = None
    db.commit()


def expire_download_if_needed(
    db: Session,
    download: Download,
    storage: LocalTemporaryStorage,
    now: datetime | None = None,
) -> bool:
    if download.status != 'completed':
        return False
    current_time = now or datetime.now(timezone.utc)
    expires_at = download.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at > current_time:
        return False
    if download.file_path:
        try:
            storage.remove_relative_file(download.file_path)
        except StoragePathError:
            pass
    download.status = 'expired'
    download.file_path = None
    db.commit()
    return True