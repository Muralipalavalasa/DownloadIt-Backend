from typing import Protocol
from uuid import UUID

from fastapi import BackgroundTasks

from app.workers.download_worker import process_download_job_in_background


class DownloadJobQueue(Protocol):
    def enqueue(self, background_tasks: BackgroundTasks, download_id: UUID) -> None: ...


class InProcessDownloadJobQueue:
    def enqueue(self, background_tasks: BackgroundTasks, download_id: UUID) -> None:
        background_tasks.add_task(process_download_job_in_background, download_id)


download_job_queue: DownloadJobQueue = InProcessDownloadJobQueue()