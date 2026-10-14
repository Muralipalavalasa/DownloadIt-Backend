from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.download import Download
from app.models.user import User
from app.schemas.downloads import (
    DeleteDownloadResponse,
    DownloadCreateRequest,
    DownloadCreatedResponse,
    DownloadDetail,
    DownloadHistoryItem,
    DownloadStatusResponse,
)
from app.providers.errors import ProviderError, to_http_exception
from app.services.download_service import (
    create_download_job,
    expire_download_if_needed,
    get_download_for_user,
)
from app.services.job_queue import download_job_queue
from app.services.storage_service import LocalTemporaryStorage, StoragePathError

router = APIRouter()
temporary_storage = LocalTemporaryStorage()


@router.get('/downloads', response_model=list[DownloadHistoryItem])
def list_downloads(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Download]:
    return list(
        db.scalars(
            select(Download)
            .where(Download.user_id == current_user.id)
            .order_by(Download.created_at.desc())
        ).all()
    )


@router.post(
    '/downloads',
    response_model=DownloadCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_download(
    request: DownloadCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DownloadCreatedResponse:
    try:
        download = create_download_job(
            db,
            current_user,
            request.source_url,
            request.format,
            request.quality,
            request.authorized_to_download,
        )
    except ProviderError as exc:
        raise to_http_exception(exc) from exc
    download_job_queue.enqueue(background_tasks, download.id)
    return DownloadCreatedResponse(id=download.id, status='pending')


@router.get('/downloads/{download_id}', response_model=DownloadDetail)
def read_download(
    download_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Download:
    return get_download_for_user(db, download_id, current_user.id)


@router.get('/downloads/{download_id}/status', response_model=DownloadStatusResponse)
def read_download_status(
    download_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DownloadStatusResponse:
    download = get_download_for_user(db, download_id, current_user.id)
    expire_download_if_needed(db, download, temporary_storage)
    return DownloadStatusResponse(
        id=download.id,
        status=download.status,
        progress=download.progress_percent,
        downloaded_bytes=download.downloaded_bytes,
        total_bytes=download.total_bytes,
        error=download.error_message,
    )


@router.get('/downloads/{download_id}/file')
def download_file(
    download_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    download = get_download_for_user(db, download_id, current_user.id)
    expire_download_if_needed(db, download, temporary_storage)
    if download.status == 'expired':
        raise HTTPException(status_code=410, detail='This download has expired. Please start again.')
    if download.status != 'completed':
        raise HTTPException(status_code=409, detail='This download is not ready yet.')
    if not download.file_path:
        raise HTTPException(status_code=404, detail='Download file not found.')
    try:
        path = temporary_storage.resolve_relative_path(download.file_path)
    except StoragePathError as exc:
        raise HTTPException(status_code=404, detail='Download file not found.') from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Download file not found.')
    media_types = {'mp4': 'video/mp4', 'm4a': 'audio/mp4'}
    extension = 'm4a' if download.format == 'm4a' else 'mp4'
    if path.suffix.lower() != f'.{extension}':
        raise HTTPException(status_code=404, detail='Download file not found.')
    return FileResponse(
        path,
        media_type=media_types[extension],
        filename=f'DownloadIt-{download.id}.{extension}',
    )


@router.delete('/downloads/{download_id}', response_model=DeleteDownloadResponse)
def delete_download(
    download_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteDownloadResponse:
    download = get_download_for_user(db, download_id, current_user.id)
    if download.status == 'processing':
        raise HTTPException(status_code=409, detail='A processing download cannot be removed yet.')
    if download.status == 'completed':
        temporary_storage.remove_job_directory(download.id)
    db.delete(download)
    db.commit()
    return DeleteDownloadResponse(success=True)