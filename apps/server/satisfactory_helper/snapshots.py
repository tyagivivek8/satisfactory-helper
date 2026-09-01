from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import BinaryIO

# A gameplay save begins with a little-endian save-header version (14 in the installed
# build). Unreal's ServerManager metadata also uses ``.sav`` but begins with ASCII ``MSGF``;
# treating every extension match as a world made status point at a 116-byte metadata file.
MIN_SAVE_HEADER_VERSION = 1
MAX_SAVE_HEADER_VERSION = 1024


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    source_name: str
    source_relative_path: str
    source_size: int
    source_mtime_ns: int
    source_sha256: str
    snapshot_path: str
    created_at: datetime

    def to_json(self) -> dict[str, object]:
        row = asdict(self)
        row["created_at"] = self.created_at.isoformat()
        return row


@contextmanager
def _shared_read(path: Path) -> Iterator[BinaryIO]:
    """Open one source for read while allowing Satisfactory to replace its autosave."""
    if os.name != "nt":
        with path.open("rb") as handle:
            yield handle
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
        None,
        3,  # OPEN_EXISTING
        0x00000080 | 0x08000000,  # normal + sequential scan
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        raise OSError(ctypes.get_last_error(), f"could not open save for shared read: {path}")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise
    with os.fdopen(fd, "rb", closefd=True) as stream:
        yield stream


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _looks_like_world_save(path: Path) -> bool:
    """Header-only discriminator that never hands an original path to the game parser."""
    try:
        with _shared_read(path) as stream:
            prefix = stream.read(4)
    except OSError:
        return False
    if len(prefix) != 4:
        return False
    version = int.from_bytes(prefix, byteorder="little", signed=True)
    return MIN_SAVE_HEADER_VERSION <= version <= MAX_SAVE_HEADER_VERSION


class SnapshotFirewall:
    """The only component allowed to touch original save paths.

    Source operations are restricted to directory enumeration, stat, and shared read.
    All mutation is confined to ``snapshot_root``.
    """

    def __init__(
        self,
        original_root: Path,
        snapshot_root: Path,
        *,
        settle_seconds: float = 0.75,
        attempts: int = 3,
    ) -> None:
        self.original_root = original_root.resolve()
        self.snapshot_root = snapshot_root.resolve()
        self.view_root = self.snapshot_root / "view"
        self.blob_root = self.snapshot_root / "blobs"
        self.pinned_root = self.snapshot_root / "pinned"
        self.settle_seconds = settle_seconds
        self.attempts = attempts
        self._lock = Lock()
        self._last: SnapshotRecord | None = None
        self._validate_roots()

    @property
    def last(self) -> SnapshotRecord | None:
        return self._last

    def _validate_roots(self) -> None:
        if self.original_root == self.snapshot_root:
            raise ValueError("snapshot root must differ from the original save root")
        if _is_relative_to(self.snapshot_root, self.original_root):
            raise ValueError("snapshot root must be outside the original save root")
        if _is_relative_to(self.original_root, self.snapshot_root):
            raise ValueError("original save root must not sit inside the snapshot cache")

    def discover(self) -> list[Path]:
        if not self.original_root.is_dir():
            return []
        candidates: list[tuple[int, str, Path]] = []
        for path in self.original_root.rglob("*.sav"):
            try:
                resolved = path.resolve(strict=True)
                if not _is_relative_to(resolved, self.original_root):
                    continue
                info = resolved.stat()
            except (FileNotFoundError, OSError):
                continue
            if not _looks_like_world_save(resolved):
                continue
            candidates.append((info.st_mtime_ns, str(resolved).casefold(), resolved))
        candidates.sort(reverse=True)
        return [path for _, _, path in candidates]

    def snapshot_latest(self) -> SnapshotRecord | None:
        with self._lock:
            candidates = self.discover()
            if not candidates:
                self._last = None
                return None
            source = candidates[0]
            before = source.stat()
            if (
                self._last
                and self._last.source_relative_path == str(source.relative_to(self.original_root))
                and self._last.source_size == before.st_size
                and self._last.source_mtime_ns == before.st_mtime_ns
            ):
                return self._last
            for _ in range(self.attempts):
                record = self._snapshot_if_stable(source)
                if record is not None:
                    self._last = record
                    return record
            raise RuntimeError(f"newest autosave did not become stable: {source.name}")

    def _snapshot_if_stable(self, source: Path) -> SnapshotRecord | None:
        source = source.resolve(strict=True)
        if not _is_relative_to(source, self.original_root):
            raise ValueError("refusing to snapshot a path outside the configured save root")
        first = source.stat()
        if self.settle_seconds:
            time.sleep(self.settle_seconds)
        second = source.stat()
        if (first.st_size, first.st_mtime_ns) != (second.st_size, second.st_mtime_ns):
            return None

        self.blob_root.mkdir(parents=True, exist_ok=True)
        relative = source.relative_to(self.original_root)
        temp_blob = self.blob_root / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        copied = 0
        try:
            with _shared_read(source) as reader, temp_blob.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            final = source.stat()
            if (second.st_size, second.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                temp_blob.unlink(missing_ok=True)
                return None
            if copied != final.st_size:
                temp_blob.unlink(missing_ok=True)
                return None

            sha256 = digest.hexdigest()
            blob = self.blob_root / f"{sha256}.sav"
            if blob.exists():
                temp_blob.unlink(missing_ok=True)
            else:
                os.replace(temp_blob, blob)

            view_path = self.view_root / relative
            view_path.parent.mkdir(parents=True, exist_ok=True)
            temp_view = view_path.with_name(f"{view_path.name}.{uuid.uuid4().hex}.part")
            shutil.copyfile(blob, temp_view)
            os.utime(temp_view, ns=(final.st_atime_ns, final.st_mtime_ns))
            os.replace(temp_view, view_path)

            record = SnapshotRecord(
                source_name=source.name,
                source_relative_path=str(relative),
                source_size=final.st_size,
                source_mtime_ns=final.st_mtime_ns,
                source_sha256=sha256,
                snapshot_path=str(view_path.relative_to(self.snapshot_root)),
                created_at=datetime.now(UTC),
            )
            self._write_record(record)
            return record
        finally:
            temp_blob.unlink(missing_ok=True)

    def _write_record(self, record: SnapshotRecord) -> None:
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        target = self.snapshot_root / "current.json"
        temporary = self.snapshot_root / f"current.{uuid.uuid4().hex}.part"
        temporary.write_text(json.dumps(record.to_json(), indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def pin(self, record: SnapshotRecord) -> Path:
        """Publish an immutable, token-scoped save root for one planning request.

        The rolling ``view`` mirrors autosave filenames and can therefore change while an
        agent is still thinking. A pin is copied from the content-addressed blob instead,
        so every automatic retry sees the exact bytes selected when the request began.
        """
        with self._lock:
            relative = Path(record.source_relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("snapshot record contains an unsafe relative path")

            blob = self.blob_root / f"{record.source_sha256}.sav"
            if not blob.is_file():
                raise FileNotFoundError(
                    f"immutable snapshot blob is unavailable: {record.source_sha256}"
                )

            pin_root = self.pinned_root / record.source_sha256
            target = (pin_root / relative).resolve()
            if not _is_relative_to(target, pin_root.resolve()):
                raise ValueError("pinned snapshot path escaped its private root")

            if target.is_file():
                if target.stat().st_size != record.source_size:
                    raise RuntimeError("existing pinned snapshot has the wrong size")
                if target.stat().st_mtime_ns != record.source_mtime_ns:
                    with suppress(OSError):
                        target.chmod(stat.S_IREAD | stat.S_IWRITE)
                    os.utime(
                        target,
                        ns=(record.source_mtime_ns, record.source_mtime_ns),
                    )
                    with suppress(OSError):
                        target.chmod(stat.S_IREAD)
                return pin_root.resolve()

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
            try:
                shutil.copyfile(blob, temporary)
                if temporary.stat().st_size != record.source_size:
                    raise RuntimeError("pinned snapshot copy has the wrong size")
                os.utime(
                    temporary,
                    ns=(record.source_mtime_ns, record.source_mtime_ns),
                )
                os.replace(temporary, target)
                with suppress(OSError):
                    target.chmod(stat.S_IREAD)
            finally:
                temporary.unlink(missing_ok=True)
            return pin_root.resolve()

    def source_fingerprint(self, path: Path) -> tuple[int, int, str]:
        """Read-only helper used by safety verification and tests."""
        resolved = path.resolve(strict=True)
        if not _is_relative_to(resolved, self.original_root):
            raise ValueError("path is outside the original save root")
        info = resolved.stat()
        digest = hashlib.sha256()
        with _shared_read(resolved) as reader:
            while chunk := reader.read(1024 * 1024):
                digest.update(chunk)
        return info.st_size, info.st_mtime_ns, digest.hexdigest()

    def make_view_read_only(self) -> None:
        """Best-effort hardening after publication; never touches original saves."""
        for path in self.view_root.rglob("*.sav"):
            try:
                path.chmod(stat.S_IREAD)
            except OSError:
                continue
