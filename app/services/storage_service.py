import shutil
import uuid
import os
from pathlib import Path

from app.core.config import settings


class StoragePathError(ValueError):
    pass


class LocalTemporaryStorage:
    _allowed_extensions = {'.mp4', '.m4a'}

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.download_storage_dir).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def create_destination(self, job_id: uuid.UUID, extension: str) -> Path:
        if extension not in self._allowed_extensions:
            raise StoragePathError('Unsupported storage extension.')
        job_directory = self.root / str(job_id)
        job_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(job_directory, 0o700)
        destination = job_directory / f'{uuid.uuid4().hex}{extension}'
        self._ensure_inside_root(destination)
        return destination

    def relative_path(self, path: Path) -> str:
        return self._ensure_inside_root(path).relative_to(self.root).as_posix()

    def resolve_relative_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or '..' in candidate.parts or len(candidate.parts) != 2:
            raise StoragePathError('Invalid storage path.')
        try:
            uuid.UUID(candidate.parts[0])
            if uuid.UUID(Path(candidate.parts[1]).stem).hex != Path(candidate.parts[1]).stem:
                raise ValueError
        except ValueError as exc:
            raise StoragePathError('Invalid generated storage path.') from exc
        if Path(candidate.parts[1]).suffix.lower() not in self._allowed_extensions:
            raise StoragePathError('Invalid storage extension.')
        return self._ensure_inside_root(self.root / candidate)

    def remove_relative_file(self, relative_path: str) -> None:
        path = self.resolve_relative_path(relative_path)
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)

    def remove_job_directory(self, job_id: uuid.UUID) -> None:
        directory = self._ensure_inside_root(self.root / str(job_id))
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
        elif directory.is_dir():
            shutil.rmtree(directory)

    def remove_job_directory_name(self, directory_name: str) -> None:
        directory = self._ensure_inside_root(self.root / directory_name)
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
        elif directory.is_dir():
            shutil.rmtree(directory)

    def _ensure_inside_root(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise StoragePathError('Storage path escaped the configured root.') from exc
        return resolved