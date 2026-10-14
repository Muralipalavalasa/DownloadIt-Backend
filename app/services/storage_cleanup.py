from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.download import Download
from app.services.download_service import expire_download_if_needed
from app.services.storage_service import LocalTemporaryStorage, StoragePathError


def cleanup_temporary_storage(
    db: Session,
    storage: LocalTemporaryStorage | None = None,
    now: datetime | None = None,
) -> int:
    active_storage = storage or LocalTemporaryStorage()
    current_time = now or datetime.now(timezone.utc)
    jobs = list(db.scalars(select(Download)).all())
    jobs_by_id = {job.id: job for job in jobs}
    removed_items = 0

    for job in jobs:
        if expire_download_if_needed(db, job, active_storage, current_time):
            removed_items += 1

    for entry in active_storage.root.iterdir():
        if entry.name == '.gitkeep':
            continue
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink(missing_ok=True)
            removed_items += 1
            continue
        try:
            job_id = UUID(entry.name)
        except ValueError:
            active_storage.remove_job_directory_name(entry.name)
            removed_items += 1
            continue

        job = jobs_by_id.get(job_id)
        if job is None or job.status in {'expired', 'failed'}:
            active_storage.remove_job_directory(job_id)
            removed_items += 1
            continue
        if job.status in {'pending', 'processing'}:
            continue

        allowed_file = None
        if job.file_path:
            try:
                allowed_file = active_storage.resolve_relative_path(job.file_path)
            except StoragePathError:
                allowed_file = None
        for stored_file in entry.iterdir():
            if stored_file != allowed_file:
                if stored_file.is_symlink() or stored_file.is_file():
                    stored_file.unlink(missing_ok=True)
                    removed_items += 1

    return removed_items