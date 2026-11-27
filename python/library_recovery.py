"""Plan and perform verified recovery into an explicitly selected directory."""

import ctypes
import errno
import os
import re
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
class Mount:
    point: Path
    root: Path
    device: bytes


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
    mounts = _linux_mounts()
    if any(_inside(library, output) or _inside(output, library) or _share_backing_tree(library, output, mounts)
           for library in (original_root, current_root)):
        raise RecoveryError("Choose a recovery destination separate from both libraries.")
    return original_root, current_root, output


def _guard(root: Directory) -> None:
    if _directory(root.path) != root:
        raise RecoveryError("A selected root folder changed. Compare again.")


def _decode_mount_path(value: bytes) -> Path:
    decoded = re.sub(rb"\\([0-7]{3})", lambda match: bytes([int(match[1], 8)]), value)
    return Path(os.fsdecode(decoded))


def _linux_mounts() -> tuple[Mount, ...]:
    if not sys.platform.startswith("linux"):
        return ()
    # Bind mounts can have the same st_dev as their parent.
    try:
        lines = Path("/proc/self/mountinfo").read_bytes().splitlines()
    except OSError as error:
        raise RecoveryError("Couldn’t check Linux mount boundaries.") from error
    mounts = []
    for line in lines:
        _, _, device, root, point, *_ = line.split()
        mounts.append(Mount(_decode_mount_path(point), _decode_mount_path(root), device))
    return tuple(mounts)


def _linux_mount_points() -> set[Path]:
    return {mount.point for mount in _linux_mounts()}


def _backing_paths(directory: Directory, mounts: tuple[Mount, ...]) -> set[tuple[bytes, Path]]:
    matches = [mount for mount in mounts if directory.path.is_relative_to(mount.point)]
    depth = max((len(mount.point.parts) for mount in matches), default=0)
    return {
        (mount.device, mount.root / directory.path.relative_to(mount.point))
        for mount in matches if len(mount.point.parts) == depth
    }


def _share_backing_tree(first: Directory, second: Directory, mounts: tuple[Mount, ...]) -> bool:
    return any(
        first_device == second_device and (first_path.is_relative_to(second_path) or second_path.is_relative_to(first_path))
        for first_device, first_path in _backing_paths(first, mounts)
        for second_device, second_path in _backing_paths(second, mounts)
    )


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
    mounts = _linux_mount_points()
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
        if (is_redirect(info) or not stat.S_ISDIR(info.st_mode)
                or info.st_dev != root.identity[0] or folder in mounts):
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
    tree = original.path.joinpath(*_parts(item.path))
    mounts = _linux_mount_points()
    if any(parent in mounts for parent in tree.parents if parent.is_relative_to(original.path)
           and parent != original.path):
        raise RecoveryError("This folder crosses into another mounted filesystem.")
    entries = []

    def visit(path, info):
        _cancel_if_requested(cancelled)
        if is_redirect(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise RecoveryError("This folder contains a link or special item and was left untouched.")
        if info.st_dev != original.identity[0] or tree / path in mounts:
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


def _linux_publish(rename, destination: Directory, stage: Directory, relative: Path) -> int:
    """Use opened directories for both sides of the exclusive rename."""
    mounts = _linux_mount_points()
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(destination.path, flags)
    try:
        if _identity(os.fstat(root_fd)) != destination.identity:
            raise RecoveryError("The recovery destination changed. Compare again.")
        stage_fd = os.open(stage.path.name, flags, dir_fd=root_fd)
        try:
            if _identity(os.fstat(stage_fd)) != stage.identity or stage.path in mounts:
                raise RecoveryError("The temporary recovery folder changed.")
            parent_fd = os.dup(root_fd)
            try:
                parent = destination.path
                for part in relative.parts[:-1]:
                    parent /= part
                    if parent in mounts:
                        raise DestinationConflict("The output path passes through a mounted folder.")
                    try:
                        os.mkdir(part, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    try:
                        child_fd = os.open(part, flags, dir_fd=parent_fd)
                    except OSError as error:
                        if error.errno in (errno.ENOTDIR, errno.ELOOP):
                            raise DestinationConflict("The output path passes through a file or link.") from error
                        raise
                    os.close(parent_fd)
                    parent_fd = child_fd
                    if os.fstat(parent_fd).st_dev != destination.identity[0]:
                        raise DestinationConflict("The output path passes through a mounted folder.")
                result = rename(stage_fd, b"content", parent_fd, os.fsencode(relative.name), 1)  # RENAME_NOREPLACE
                return ctypes.get_errno() if result else 0
            finally:
                os.close(parent_fd)
        finally:
            os.close(stage_fd)
    finally:
        os.close(root_fd)


def _publish(destination: Directory, stage: Directory, relative: Path) -> None:
    """Publish without replacing even an empty folder created by another process."""
    target = _target(destination, relative, create_parents=not sys.platform.startswith("linux"))
    source = stage.path / "content"
    _guard(stage)
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
            code = ctypes.get_errno() if result else 0
        elif sys.platform.startswith("linux"):
            rename = libc.renameat2
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            code = _linux_publish(rename, destination, stage, relative)
        else:
            raise AttributeError
    except AttributeError as error:
        raise RecoveryError("This system doesn’t support publishing recovered folders without replacement.") from error
    if code:
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


def _apply_metadata(path: Path, info: os.stat_result, *, owner_access: bool = False) -> None:
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    mode = info.st_mode & 0o777
    os.chmod(path, mode | 0o700 if owner_access else mode)


def _stage_copy(plan: RecoveryPlan, folder: PlannedFolder, payload: Path, cancelled,
                restrictive_dirs: list[Directory]) -> int | None:
    dirs = []
    final_mode = None
    source = plan.original.path.joinpath(*_parts(folder.item.path))
    mounts = _linux_mount_points()
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
                if live.st_dev != plan.original.identity[0] or folder_path in mounts:
                    raise RecoveryError("A source path moved to another filesystem. Compare again.")
            _copy_file(incoming, output, info, cancelled)
    for output, info in reversed(dirs):
        if os.name != "nt" and info.st_mode & 0o700 != 0o700:
            if output == payload and sys.platform.startswith("linux") and not info.st_mode & stat.S_IWUSR:
                # Linux may require write access to update ".." when moving
                # the payload across parents. Restore its mode after rename.
                final_mode = info.st_mode & 0o777
                _apply_metadata(output, info, owner_access=True)
                continue
            restrictive_dirs.append(Directory(output, _identity(output.lstat())))
        _apply_metadata(output, info)
    return final_mode


def _recover_folder(plan: RecoveryPlan, folder: PlannedFolder, cancelled) -> EntryResult:
    stage = None
    restrictive_dirs = []
    published = False
    payload_fd = None
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
        final_mode = _stage_copy(plan, folder, payload, cancelled, restrictive_dirs)
        _source_entries(plan.original, plan.current, folder.item, cancelled)
        _cancel_if_requested(cancelled)
        if final_mode is not None:
            _guard(stage)
            payload_fd = os.open(payload, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _publish(plan.destination, stage, folder.target)
        published = True
        result = EntryResult(path, Status.RECOVERED)
        if payload_fd is not None:
            try:
                os.fchmod(payload_fd, final_mode)
            except OSError:
                result = EntryResult(path, Status.RECOVERED, "Couldn’t restore the recovered folder’s permissions.")
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
        if payload_fd is not None:
            os.close(payload_fd)
        if stage is not None:
            try:
                _guard(plan.destination)
                _guard(stage)
                if any(mount.is_relative_to(stage.path) for mount in _linux_mount_points()):
                    raise RecoveryError("The temporary folder contains a mount.")
                if not published:
                    # Restore access only on directories created by this transfer,
                    # starting with their parents so nested paths are reachable.
                    for directory in reversed(restrictive_dirs):
                        _guard(directory)
                        directory.path.chmod(0o700)
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
