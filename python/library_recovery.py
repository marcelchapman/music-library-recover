"""Plan and perform verified recovery into an explicitly selected directory."""

import ctypes
import errno
import os
import shutil
import stat
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp

from audio_classification import AudioFormat, Classification, ClassificationError, classify_folder, file_signature, is_redirect

CATEGORIES = {
    AudioFormat.MP3: "MP3",
    AudioFormat.AIFF: "AIFF",
    AudioFormat.MIXED: "MIXED",
    AudioFormat.UNCLASSIFIED: "UNCLASSIFIED",
    AudioFormat.NO_AUDIO: "UNCLASSIFIED",
}
CHUNK_SIZE = 1024 * 1024


class RecoveryError(Exception):
    """A recovery could not safely proceed."""


class RecoveryCancelled(Exception):
    """The user asked the worker to stop."""


class DestinationConflict(RecoveryError):
    """An output already exists or is blocked by another item."""


class Status(Enum):
    RECOVERED = "Recovered"
    SKIPPED = "Skipped"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class EntryResult:
    path: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class RecoveryItem:
    path: str
    classification: Classification | None


@dataclass(frozen=True)
class Directory:
    path: Path
    identity: tuple[int, int]


@dataclass(frozen=True)
class PlannedFolder:
    item: RecoveryItem
    target: Path
    entries: tuple[tuple[Path, os.stat_result], ...]
    size: int


@dataclass(frozen=True)
class RecoveryPlan:
    original: Directory
    current: Directory
    destination: Directory
    folders: tuple[PlannedFolder, ...]
    skipped: tuple[EntryResult, ...]

    @property
    def size(self) -> int:
        return sum(folder.size for folder in self.folders)

    @property
    def selected_count(self) -> int:
        return len(self.folders) + len(self.skipped)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _directory_info(path: Path) -> os.stat_result:
    info = path.lstat()
    if is_redirect(info) or not stat.S_ISDIR(info.st_mode):
        raise RecoveryError("A folder path includes a link, redirect or item that isn’t a directory.")
    return info


def _directory(path: Path) -> Directory:
    path = Path(path).absolute()
    if ".." in path.parts:
        raise RecoveryError("Choose the folder directly and try again.")
    try:
        # Inspect components before resolving, so resolving cannot hide a link.
        for ancestor in reversed(path.parents):
            _directory_info(ancestor)
        info = _directory_info(path)
        canonical = path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as error:
        raise RecoveryError("A selected folder is unavailable. Check the folder and try again.") from error
    return Directory(canonical, _identity(info))


def _inside(ancestor: Directory, child: Directory) -> bool:
    if child.path.is_relative_to(ancestor.path):
        return True
    # Filesystem identity also catches aliases of spelling on case-insensitive volumes.
    return any(_identity(_directory_info(path)) == ancestor.identity
               for path in (child.path, *child.path.parents))


def validate_destination(original: Path, current: Path, destination: Path) -> tuple[Directory, Directory, Directory]:
    original_root, current_root, output = map(_directory, (original, current, destination))
    if any(_inside(library, output) or _inside(output, library) for library in (original_root, current_root)):
        raise RecoveryError("Choose a recovery destination separate from both libraries.")
    return original_root, current_root, output


def _guard(root: Directory) -> None:
    if _directory(root.path) != root:
        raise RecoveryError("A selected root folder changed. Compare again.")


def _parts(relative: str) -> tuple[str, ...]:
    parts = tuple(relative.split("/"))
    for part in parts:
        if part in ("", ".", "..") or Path(part).name != part or Path(part).is_reserved():
            raise RecoveryError("The folder path is invalid. Compare again.")
        if os.name == "nt" and (":" in part or part.endswith((" ", "."))):
            raise RecoveryError("The folder name cannot be safely recovered on this system.")
    return parts


def _target(root: Directory, relative: Path, *, create_parents: bool = False) -> Path:
    _guard(root)
    target = root.path / relative
    if (relative.is_absolute() or ".." in relative.parts or target == root.path
            or not target.is_relative_to(root.path)):
        raise RecoveryError("A recovery path is outside the selected destination.")
    folder = root.path
    for part in relative.parts[:-1]:
        folder /= part
        if create_parents:
            try:
                folder.mkdir()
            except FileExistsError:
                pass
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return target
        if is_redirect(info) or not stat.S_ISDIR(info.st_mode) or info.st_dev != root.identity[0]:
            raise DestinationConflict("The output path passes through an existing file, link or mounted folder.")
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    raise DestinationConflict("An item already exists at the recovery path.")


def _check_current(current: Directory, path: str) -> None:
    _guard(current)
    folder = current.path
    for part in _parts(path):
        folder /= part
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return
        if is_redirect(info):
            raise RecoveryError("The corresponding current-library path includes a redirect. Compare again.")
        if not stat.S_ISDIR(info.st_mode):
            return
    raise RecoveryError("This folder is now present in the current library. Compare again.")


def _cancel_if_requested(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise RecoveryCancelled


def _source_entries(original: Directory, current: Directory, item: RecoveryItem, cancelled) -> tuple:
    _guard(original)
    _check_current(current, item.path)
    expected = item.classification
    if expected is None or expected.audio_format not in CATEGORIES or not expected.fingerprint:
        raise RecoveryError("This folder needs a completed audio classification. Compare again.")
    entries = []

    def visit(path, info):
        _cancel_if_requested(cancelled)
        if is_redirect(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise RecoveryError("This folder contains a link or special item and was left untouched.")
        if info.st_dev != original.identity[0]:
            raise RecoveryError("This folder crosses into another mounted filesystem.")
        entries.append((path, info))

    try:
        latest = classify_folder(original.path, item.path, visit=visit)
    except ClassificationError as error:
        raise RecoveryError(str(error)) from error
    if latest.fingerprint != expected.fingerprint or latest != expected:
        raise RecoveryError("The source contents or audio format changed. Compare again.")
    return tuple(entries)


def _require_space(root: Directory, estimated: int) -> None:
    try:
        free = shutil.disk_usage(root.path).free
    except OSError as error:
        raise RecoveryError("Couldn’t check available space on the recovery destination.") from error
    if estimated > free:
        raise RecoveryError("There isn’t enough free space at the recovery destination.")


def prepare_recovery(original: Path, current: Path, destination: Path, selected: Iterable[RecoveryItem],
                     *, cancelled=lambda: False) -> RecoveryPlan:
    roots = validate_destination(original, current, destination)
    original_root, current_root, output = roots
    folders = []
    skipped = []
    seen = set()
    for item in selected:
        _cancel_if_requested(cancelled)
        if item.path in seen:
            continue
        seen.add(item.path)
        try:
            parts = _parts(item.path)
            category = CATEGORIES.get(item.classification.audio_format) if item.classification else None
            if category is None:
                raise RecoveryError("This folder needs a completed audio classification. Compare again.")
            target = Path(category, *parts)
            _target(output, target)
            entries = _source_entries(original_root, current_root, item, cancelled)
            size = sum(info.st_size for _, info in entries if stat.S_ISREG(info.st_mode))
            folders.append(PlannedFolder(item, target, entries, size))
        except RecoveryError as error:
            skipped.append(EntryResult(item.path, Status.SKIPPED, str(error)))
        except (OSError, ValueError):
            skipped.append(EntryResult(item.path, Status.SKIPPED, "Couldn’t inspect this folder or its recovery path."))
    plan = RecoveryPlan(*roots, tuple(folders), tuple(skipped))
    _guard(output)
    _require_space(output, plan.size)
    return plan


def _publish(source: Path, target: Path) -> None:
    """Publish without replacing even an empty folder created by another process."""
    if os.name == "nt":
        try:
            os.rename(source, target)
        except FileExistsError as error:
            raise DestinationConflict("An item appeared at the recovery path during the transfer.") from error
        return
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = libc.renamex_np
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            result = rename(os.fsencode(source), os.fsencode(target), 0x00000004)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            rename = libc.renameat2
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # AT_FDCWD, RENAME_NOREPLACE
        else:
            raise AttributeError
    except AttributeError as error:
        raise RecoveryError("This system doesn’t support publishing recovered folders without replacement.") from error
    if result != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.ENOTSUP, errno.EINVAL):
            raise RecoveryError("This destination doesn’t support publishing a folder without replacement.")
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise DestinationConflict("An item appeared at the recovery path during the transfer.")
        raise OSError(code, os.strerror(code))


def _copy_file(source: Path, output: Path, approved: os.stat_result, cancelled) -> None:
    signature = file_signature(approved)
    if file_signature(source.lstat()) != signature:
        raise RecoveryError("A source file changed during recovery. Compare again.")

    # NOFOLLOW closes the final-component link window where available. NONBLOCK
    # prevents a replaced FIFO from blocking before its file type can be checked.
    flags = os.O_RDONLY
    for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_BINARY"):
        flags |= getattr(os, flag, 0)
    with os.fdopen(os.open(source, flags), "rb") as incoming:
        if file_signature(os.fstat(incoming.fileno())) != signature:
            raise RecoveryError("A source file changed during recovery. Compare again.")
        with output.open("x+b") as staged:
            expected = sha256()
            while chunk := incoming.read(CHUNK_SIZE):
                _cancel_if_requested(cancelled)
                expected.update(chunk)
                staged.write(chunk)
            if (staged.tell() != approved.st_size
                    or file_signature(os.fstat(incoming.fileno())) != signature):
                raise RecoveryError("A source file changed during recovery. Compare again.")
            staged.flush()
            os.fsync(staged.fileno())
            staged.seek(0)
            actual = sha256()
            while chunk := staged.read(CHUNK_SIZE):
                _cancel_if_requested(cancelled)
                actual.update(chunk)
            if actual.digest() != expected.digest():
                raise RecoveryError("The copied file did not pass its integrity check.")
    _apply_metadata(output, approved)


def _apply_metadata(path: Path, info: os.stat_result) -> None:
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    os.chmod(path, stat.S_IMODE(info.st_mode) & 0o777)


def _stage_copy(plan: RecoveryPlan, folder: PlannedFolder, payload: Path, cancelled) -> None:
    dirs = []
    source = plan.original.path.joinpath(*_parts(folder.item.path))
    for relative, info in folder.entries:
        _cancel_if_requested(cancelled)
        output = payload / relative
        if stat.S_ISDIR(info.st_mode):
            output.mkdir()
            dirs.append((output, info))
        else:
            # Each inventoried directory is checked again immediately before source reads.
            _guard(plan.original)
            incoming = source / relative
            folder_path = plan.original.path
            for part in incoming.parent.relative_to(folder_path).parts:
                folder_path /= part
                live = _directory_info(folder_path)
                if live.st_dev != plan.original.identity[0]:
                    raise RecoveryError("A source path moved to another filesystem. Compare again.")
            _copy_file(incoming, output, info, cancelled)
    for output, info in reversed(dirs):
        _apply_metadata(output, info)


def _recover_folder(plan: RecoveryPlan, folder: PlannedFolder, cancelled) -> EntryResult:
    stage = None
    cleanup_warning = ""
    path = folder.item.path
    try:
        _target(plan.destination, folder.target)
        _require_space(plan.destination, folder.size)
        _source_entries(plan.original, plan.current, folder.item, cancelled)
        _cancel_if_requested(cancelled)

        _guard(plan.destination)
        stage = _directory(Path(mkdtemp(prefix=".mlr-recovery-", dir=plan.destination.path)))
        payload = stage.path / "content"
        _stage_copy(plan, folder, payload, cancelled)
        _source_entries(plan.original, plan.current, folder.item, cancelled)
        _cancel_if_requested(cancelled)
        target = _target(plan.destination, folder.target, create_parents=True)
        _guard(stage)
        _cancel_if_requested(cancelled)
        _publish(payload, target)
        result = EntryResult(path, Status.RECOVERED)
    except DestinationConflict as error:
        result = EntryResult(path, Status.SKIPPED, str(error))
    except RecoveryCancelled:
        result = EntryResult(path, Status.CANCELLED, "Recovery was stopped before this folder was published.")
    except RecoveryError as error:
        result = EntryResult(path, Status.FAILED, str(error))
    except OSError as error:
        detail = "Couldn’t copy or verify this folder. Check access and available space."
        if error.errno == errno.ENOSPC:
            detail = "The recovery destination ran out of space."
        result = EntryResult(path, Status.FAILED, detail)
    finally:
        if stage is not None:
            try:
                _guard(plan.destination)
                _guard(stage)
                shutil.rmtree(stage.path)
            except (OSError, RecoveryError):
                cleanup_warning = f" Temporary files may remain in {stage.path.name}."
    if cleanup_warning:
        return EntryResult(path, result.status, (result.detail + cleanup_warning).strip())
    return result


def recover(plan: RecoveryPlan, *, cancelled=lambda: False, progress=lambda done, total, path: None) -> tuple[EntryResult, ...]:
    """Run the approved plan, reporting only verified and published folders as recovered."""
    results = list(plan.skipped)
    total = len(plan.folders)
    for index, folder in enumerate(plan.folders):
        if cancelled():
            results.extend(EntryResult(remaining.item.path, Status.CANCELLED)
                           for remaining in plan.folders[index:])
            break
        progress(index, total, folder.item.path)
        results.append(_recover_folder(plan, folder, cancelled))
    completed = sum(result.status != Status.CANCELLED for result in results[len(plan.skipped):])
    progress(completed, total, "")
    return tuple(results)
