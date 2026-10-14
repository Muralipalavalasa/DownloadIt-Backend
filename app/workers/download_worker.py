import json
import os
import shutil
import subprocess
from pathlib import Path
from time import monotonic
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.download import Download
from app.core.config import settings
from app.providers.errors import (
    FFmpegUnavailable,
    MediaNotDownloadable,
    MediaProcessingError,
    ProviderUnavailable,
    RequestedQualityUnavailable,
)
from app.providers.registry import ProviderRegistry, default_provider_registry
from app.services.download_service import (
    GENERIC_DOWNLOAD_ERROR,
    mark_completed,
    mark_failed,
    mark_processing,
    update_download_progress,
)
from app.services.storage_service import LocalTemporaryStorage, StoragePathError

temporary_storage = LocalTemporaryStorage()


def validate_downloaded_media(path: Path, expected_extension: str) -> None:
    ffprobe = shutil.which('ffprobe')
    if ffprobe is None:
        raise MediaProcessingError('Downloaded media could not be validated.')
    try:
        result = subprocess.run(
            [
                ffprobe,
                '-v', 'error',
                '-show_entries', 'format=format_name:stream=codec_type',
                '-of', 'json',
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise MediaProcessingError('Downloaded media could not be validated.')
        metadata = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise MediaProcessingError('Downloaded media could not be validated.') from exc

    format_info = metadata.get('format')
    format_names = set(format_info.get('format_name', '').split(',')) if isinstance(format_info, dict) else set()
    streams = metadata.get('streams')
    stream_types = {
        stream.get('codec_type')
        for stream in streams
        if isinstance(stream, dict)
    } if isinstance(streams, list) else set()

    if expected_extension == '.mp4':
        valid_media = 'mp4' in format_names and 'video' in stream_types
    elif expected_extension == '.m4a':
        valid_media = 'm4a' in format_names and 'audio' in stream_types and 'video' not in stream_types
    else:
        valid_media = False
    if not valid_media:
        raise MediaProcessingError('Downloaded media does not match the selected format.')


def process_download_job(
    db: Session,
    download_id: UUID,
    registry: ProviderRegistry | None = None,
    storage: LocalTemporaryStorage | None = None,
) -> Download | None:
    download = db.get(Download, download_id)
    if download is None or download.status != 'pending':
        return download

    active_registry = registry or default_provider_registry
    active_storage = storage or temporary_storage
    destination: Path | None = None
    last_progress_write = 0.0

    def report_progress(downloaded_bytes: int, total_bytes: int | None) -> None:
        nonlocal last_progress_write
        if downloaded_bytes > settings.download_max_bytes:
            raise MediaNotDownloadable('The media file exceeds the configured size limit.')
        current_time = monotonic()
        if last_progress_write == 0.0 or current_time - last_progress_write >= 1.0:
            update_download_progress(db, download, downloaded_bytes, total_bytes)
            last_progress_write = current_time

    mark_processing(db, download)
    try:
        provider = active_registry.get_provider(download.source_url)
        media_info = provider.analyze(download.source_url)
        download.platform = media_info.platform or provider.platform_for(download.source_url).value
        selected_format = provider.select_format(media_info, download.format, download.quality)
        destination = active_storage.create_destination(download.id, selected_format.extension)
        provider.download(download.source_url, destination, selected_format, report_progress)
        if not destination.is_file() or destination.is_symlink() or destination.stat().st_size == 0:
            raise MediaProcessingError('The downloaded file is unavailable.')
        os.chmod(destination, 0o600)
        file_size = destination.stat().st_size
        if file_size > settings.download_max_bytes:
            raise MediaNotDownloadable('The media file exceeds the configured size limit.')
        relative_path = active_storage.relative_path(destination)
        job_files = list(destination.parent.iterdir())
        if job_files != [destination]:
            raise MediaProcessingError('Temporary download files remain after processing.')
        validate_downloaded_media(destination, selected_format.extension)
        mark_completed(db, download, media_info.title, relative_path, file_size)
    except Exception as exc:
        try:
            active_storage.remove_job_directory(download.id)
        except (OSError, StoragePathError):
            pass
        db.refresh(download)
        safe_error = GENERIC_DOWNLOAD_ERROR
        if isinstance(exc, FFmpegUnavailable):
            safe_error = 'FFmpeg is required for this video/audio operation.'
        elif isinstance(exc, RequestedQualityUnavailable):
            safe_error = 'Requested quality is not available for this video.'
        elif isinstance(exc, ProviderUnavailable):
            safe_error = 'Media provider is temporarily unavailable.'
        mark_failed(db, download, safe_error)
    return download


def process_download_job_in_background(download_id: UUID) -> None:
    with SessionLocal() as db:
        process_download_job(db, download_id)