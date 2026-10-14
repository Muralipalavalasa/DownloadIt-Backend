from datetime import datetime, timedelta, timezone
import json
import stat
import subprocess
from uuid import UUID, uuid4

import pytest
from app.core.config import settings
from app.models.download import Download
from app.models.user import User
from app.providers.base import MediaFormat, MediaMetadata, Platform
from app.providers.errors import MediaProcessingError
from app.providers.registry import ProviderRegistry
from app.services.storage_cleanup import cleanup_temporary_storage
from app.services.storage_service import LocalTemporaryStorage, StoragePathError
from app.workers.download_worker import process_download_job
import app.workers.download_worker as download_worker


SAMPLE_URL = 'https://samplelib.com/mp4/sample-5s.mp4'


class FakeProvider:
    platform = Platform.DIRECT
    display_name = 'Fake direct provider'

    @property
    def is_enabled(self) -> bool:
        return True

    def __init__(self, session, job_id: UUID, should_fail: bool = False) -> None:
        self.session = session
        self.job_id = job_id
        self.should_fail = should_fail
        self.status_during_analyze: str | None = None
        self.progress_events = [(10, 100), (20, 100), (60, 100)]
        self.persisted_progress: list[int | None] = []

    def can_handle(self, source_url: str) -> bool:
        return source_url.startswith('https://samplelib.com/')

    def platform_for(self, _source_url: str) -> Platform:
        return Platform.DIRECT

    def validate_url(self, _source_url: str) -> None:
        return None

    def supports_format(self, output_format: str, quality: str) -> bool:
        return output_format == 'mp4' and quality == 'source'

    def validate_download_options(self, output_format: str, quality: str) -> None:
        if not self.supports_format(output_format, quality):
            raise MediaProcessingError('unsupported fake format')

    def select_format(self, metadata: MediaMetadata, output_format: str, quality: str) -> MediaFormat:
        return next(
            item for item in metadata.available_formats
            if item.format == output_format and item.quality == quality
        )

    def select_format(self, metadata: MediaMetadata, output_format: str, quality: str) -> MediaFormat:
        return next(
            item for item in metadata.available_formats
            if item.format == output_format and item.quality == quality
        )

    def analyze(self, source_url: str) -> MediaMetadata:
        self.status_during_analyze = self.session.get(Download, self.job_id).status
        if self.should_fail:
            raise MediaProcessingError('private upstream error detail')
        return MediaMetadata(
            title='Authorized sample video',
            duration=None,
            thumbnail=None,
            available_formats=(MediaFormat('mp4', 'source'),),
            content_type='video/mp4',
            extension='.mp4',
        )

    def download(self, source_url: str, destination, media_format: MediaFormat, progress_callback=None) -> None:
        content = b'test-only-mp4-content'
        destination.write_bytes(content)
        if progress_callback is not None:
            for downloaded, total in self.progress_events:
                progress_callback(downloaded, total)
                self.persisted_progress.append(
                    self.session.get(Download, self.job_id).progress_percent
                )


def create_job(session, user_id: UUID, **overrides) -> Download:
    values = {
        'user_id': user_id,
        'source_url': SAMPLE_URL,
        'platform': 'direct',
        'status': 'pending',
        'format': 'mp4',
        'quality': 'source',
    }
    values.update(overrides)
    job = Download(**values)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_worker_transitions_pending_to_processing_and_completed(test_session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download_worker, 'validate_downloaded_media', lambda _path, _extension: None)
    storage = LocalTemporaryStorage(tmp_path)
    with test_session_factory() as session:
        user = User(email='worker@example.com')
        session.add(user)
        session.commit()
        job = create_job(session, user.id)
        provider = FakeProvider(session, job.id)
        registry = ProviderRegistry([provider])

        processed = process_download_job(session, job.id, registry=registry, storage=storage)

        assert processed is not None
        assert provider.status_during_analyze == 'processing'
        assert processed.status == 'completed'
        assert processed.title == 'Authorized sample video'
        assert processed.file_path is not None
        assert processed.progress_percent == 100
        assert processed.downloaded_bytes == len(b'test-only-mp4-content')
        assert processed.total_bytes == len(b'test-only-mp4-content')
        assert processed.expires_at == processed.completed_at + timedelta(
            minutes=settings.download_file_expiration_minutes
        )
        path = storage.resolve_relative_path(processed.file_path)
        assert path.is_file()
        assert path.read_bytes() == b'test-only-mp4-content'
        assert path.parent.name == str(job.id)
        assert UUID(path.stem)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_worker_records_safe_failure(test_session_factory, tmp_path) -> None:
    storage = LocalTemporaryStorage(tmp_path)
    with test_session_factory() as session:
        user = User(email='failed-worker@example.com')
        session.add(user)
        session.commit()
        job = create_job(session, user.id)

        registry = ProviderRegistry([FakeProvider(session, job.id, should_fail=True)])
        processed = process_download_job(session, job.id, registry=registry, storage=storage)

        assert processed is not None
        assert processed.status == 'failed'
        assert processed.error_message == "We couldn't prepare this download. Please try again."
        assert 'private upstream error detail' not in processed.error_message
        assert list(tmp_path.iterdir()) == []


def test_worker_throttles_progress_updates(test_session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download_worker, 'validate_downloaded_media', lambda _path, _extension: None)
    times = iter([10.0, 10.4, 11.1])
    monkeypatch.setattr(download_worker, 'monotonic', lambda: next(times))
    storage = LocalTemporaryStorage(tmp_path)
    with test_session_factory() as session:
        user = User(email='progress-worker@example.com')
        session.add(user)
        session.commit()
        job = create_job(session, user.id)
        provider = FakeProvider(session, job.id)

        processed = process_download_job(
            session,
            job.id,
            registry=ProviderRegistry([provider]),
            storage=storage,
        )

        assert processed is not None
        assert provider.persisted_progress == [10, 10, 60]
        assert processed.progress_percent == 100


def test_worker_removes_file_that_exceeds_size_cap(test_session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'download_max_bytes', 5)
    storage = LocalTemporaryStorage(tmp_path)
    with test_session_factory() as session:
        user = User(email='oversize-worker@example.com')
        session.add(user)
        session.commit()
        job = create_job(session, user.id)
        provider = FakeProvider(session, job.id)

        processed = process_download_job(
            session,
            job.id,
            registry=ProviderRegistry([provider]),
            storage=storage,
        )

        assert processed is not None
        assert processed.status == 'failed'
        assert list(tmp_path.iterdir()) == []


def test_media_validation_rejects_wrong_container(tmp_path, monkeypatch) -> None:
    media_path = tmp_path / 'generated.mp4'
    media_path.write_bytes(b'not an mp4')
    probe_output = json.dumps({'format': {'format_name': 'matroska,webm'}, 'streams': [{'codec_type': 'video'}]})
    monkeypatch.setattr(download_worker.shutil, 'which', lambda _name: '/usr/bin/ffprobe')
    monkeypatch.setattr(
        download_worker.subprocess,
        'run',
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, probe_output, ''),
    )

    with pytest.raises(MediaProcessingError):
        download_worker.validate_downloaded_media(media_path, '.mp4')


def test_media_validation_requires_expected_streams(tmp_path, monkeypatch) -> None:
    media_path = tmp_path / 'generated.mp4'
    media_path.write_bytes(b'not an mp4')
    probe_output = json.dumps({'format': {'format_name': 'mov,mp4,m4a'}, 'streams': [{'codec_type': 'audio'}]})
    monkeypatch.setattr(download_worker.shutil, 'which', lambda _name: '/usr/bin/ffprobe')
    monkeypatch.setattr(
        download_worker.subprocess,
        'run',
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, probe_output, ''),
    )

    with pytest.raises(MediaProcessingError):
        download_worker.validate_downloaded_media(media_path, '.mp4')


def test_media_validation_requires_ffprobe(tmp_path, monkeypatch) -> None:
    media_path = tmp_path / 'generated.mp4'
    media_path.write_bytes(b'not an mp4')
    monkeypatch.setattr(download_worker.shutil, 'which', lambda _name: None)

    with pytest.raises(MediaProcessingError):
        download_worker.validate_downloaded_media(media_path, '.mp4')


def test_worker_cleans_job_when_media_validation_fails(test_session_factory, tmp_path, monkeypatch) -> None:
    def reject_media(_path, _extension) -> None:
        raise MediaProcessingError('invalid container')

    monkeypatch.setattr(download_worker, 'validate_downloaded_media', reject_media)
    storage = LocalTemporaryStorage(tmp_path)
    with test_session_factory() as session:
        user = User(email='invalid-media-worker@example.com')
        session.add(user)
        session.commit()
        job = create_job(session, user.id)
        provider = FakeProvider(session, job.id)

        processed = process_download_job(session, job.id, registry=ProviderRegistry([provider]), storage=storage)

        assert processed is not None
        assert processed.status == 'failed'
        assert processed.file_path is None
        assert list(tmp_path.iterdir()) == []


def test_worker_removes_residual_partial_files(test_session_factory, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download_worker, 'validate_downloaded_media', lambda _path, _extension: None)
    storage = LocalTemporaryStorage(tmp_path)
    with test_session_factory() as session:
        user = User(email='partial-worker@example.com')
        session.add(user)
        session.commit()
        job = create_job(session, user.id)
        provider = FakeProvider(session, job.id)
        original_download = provider.download

        def write_partial(source_url, destination, media_format, progress_callback=None) -> None:
            original_download(source_url, destination, media_format, progress_callback)
            destination.with_suffix(destination.suffix + '.part').write_bytes(b'partial')

        monkeypatch.setattr(provider, 'download', write_partial)
        processed = process_download_job(session, job.id, registry=ProviderRegistry([provider]), storage=storage)

        assert processed is not None
        assert processed.status == 'failed'
        assert processed.file_path is None
        assert list(tmp_path.iterdir()) == []


def test_status_endpoint_returns_null_progress(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    created = client.post('/api/downloads', json={'source_url': SAMPLE_URL}, headers=headers)
    response = client.get(f"/api/downloads/{created.json()['id']}/status", headers=headers)

    assert response.status_code == 200
    assert response.json()['status'] == 'pending'
    assert response.json()['progress'] is None


def test_status_endpoint_returns_persisted_progress(
    client,
    create_authenticated_user,
    test_session_factory,
) -> None:
    from app.models.download import Download as DownloadModel

    registered, headers = create_authenticated_user('progress-api@example.com')
    created = client.post('/api/downloads', json={'source_url': SAMPLE_URL}, headers=headers)
    with test_session_factory() as session:
        download = session.get(DownloadModel, UUID(created.json()['id']))
        assert download is not None
        download.status = 'processing'
        download.progress_percent = 42
        download.downloaded_bytes = 420
        download.total_bytes = 1000
        session.commit()

    response = client.get(f"/api/downloads/{created.json()['id']}/status", headers=headers)

    assert response.status_code == 200
    assert response.json()['progress'] == 42
    assert response.json()['downloaded_bytes'] == 420
    assert response.json()['total_bytes'] == 1000
    assert registered['id']


def test_unallowlisted_download_source_is_rejected(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    response = client.post(
        '/api/downloads',
        json={'source_url': 'https://untrusted.example/video.mp4', 'quality': 'source'},
        headers=headers,
    )
    assert response.status_code == 400


def test_file_endpoint_rejects_pending_job(client, create_authenticated_user) -> None:
    _, headers = create_authenticated_user()
    created = client.post('/api/downloads', json={'source_url': SAMPLE_URL}, headers=headers)
    response = client.get(f"/api/downloads/{created.json()['id']}/file", headers=headers)
    assert response.status_code == 409


def test_user_cannot_download_another_users_completed_file(
    client,
    create_authenticated_user,
    test_session_factory,
    monkeypatch,
    tmp_path,
) -> None:
    from app.api.routes import downloads as downloads_route

    owner, owner_headers = create_authenticated_user('file-owner@example.com')
    _, other_headers = create_authenticated_user('file-other@example.com')
    storage = LocalTemporaryStorage(tmp_path)
    monkeypatch.setattr(downloads_route, 'temporary_storage', storage)
    job_id = uuid4()
    destination = storage.create_destination(job_id, '.mp4')
    destination.write_bytes(b'owned-test-video')
    with test_session_factory() as session:
        job = create_job(
            session,
            UUID(owner['id']),
            id=job_id,
            status='completed',
            quality='source',
            file_path=storage.relative_path(destination),
            completed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    denied = client.get(f'/api/downloads/{job.id}/file', headers=other_headers)
    allowed = client.get(f'/api/downloads/{job.id}/file', headers=owner_headers)

    assert denied.status_code == 404
    assert allowed.status_code == 200
    assert allowed.content == b'owned-test-video'


def test_file_endpoint_rejects_expired_and_missing_files(
    client,
    create_authenticated_user,
    test_session_factory,
    monkeypatch,
    tmp_path,
) -> None:
    from app.api.routes import downloads as downloads_route
    registered, headers = create_authenticated_user('file-state@example.com')
    storage = LocalTemporaryStorage(tmp_path)
    monkeypatch.setattr(downloads_route, 'temporary_storage', storage)
    expired_id = uuid4()
    expired_path = storage.create_destination(expired_id, '.mp4')
    expired_path.write_bytes(b'expired')
    missing_id = uuid4()

    with test_session_factory() as session:
        expired_job = create_job(
            session,
            UUID(registered['id']),
            id=expired_id,
            status='completed',
            file_path=storage.relative_path(expired_path),
            completed_at=datetime.now(timezone.utc) - timedelta(minutes=45),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        missing_job = create_job(
            session,
            UUID(registered['id']),
            id=missing_id,
            status='completed',
            file_path=f'{missing_id}/{uuid4().hex}.mp4',
            completed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    expired_response = client.get(f'/api/downloads/{expired_job.id}/file', headers=headers)
    missing_response = client.get(f'/api/downloads/{missing_job.id}/file', headers=headers)

    assert expired_response.status_code == 410
    assert not expired_path.exists()
    assert missing_response.status_code == 404


@pytest.mark.parametrize('path', ['../../etc/passwd', '/etc/passwd', 'job/../../etc/passwd'])
def test_storage_rejects_path_traversal(tmp_path, path: str) -> None:
    storage = LocalTemporaryStorage(tmp_path)
    with pytest.raises(StoragePathError):
        storage.resolve_relative_path(path)


def test_storage_generates_non_user_controlled_filenames(tmp_path) -> None:
    storage = LocalTemporaryStorage(tmp_path)
    job_id = uuid4()
    destination = storage.create_destination(job_id, '.mp4')

    assert destination.parent.name == str(job_id)
    assert UUID(destination.stem)
    assert destination.name != 'sample-5s.mp4'
    assert storage.resolve_relative_path(storage.relative_path(destination)) == destination


def test_cleanup_expires_files_removes_orphans_and_keeps_active_jobs(test_session_factory, tmp_path) -> None:
    storage = LocalTemporaryStorage(tmp_path)
    marker = tmp_path / '.gitkeep'
    marker.write_text('')
    now = datetime.now(timezone.utc)
    with test_session_factory() as session:
        user = User(email='cleanup@example.com')
        session.add(user)
        session.commit()
        pending = create_job(session, user.id)
        pending_file = storage.create_destination(pending.id, '.mp4')
        pending_file.write_bytes(b'active')
        expired = create_job(
            session,
            user.id,
            status='completed',
            completed_at=now - timedelta(hours=1),
            expires_at=now - timedelta(seconds=1),
        )
        expired_file = storage.create_destination(expired.id, '.mp4')
        expired_file.write_bytes(b'expired')
        expired.file_path = storage.relative_path(expired_file)
        session.commit()
        orphan_id = uuid4()
        orphan_file = storage.create_destination(orphan_id, '.mp4')
        orphan_file.write_bytes(b'orphan')

        cleanup_temporary_storage(session, storage, now)
        session.refresh(expired)
        session.refresh(pending)

        assert expired.status == 'expired'
        assert not expired_file.exists()
        assert pending.status == 'pending'
        assert pending_file.exists()
        assert not (tmp_path / str(orphan_id)).exists()
        assert marker.exists()