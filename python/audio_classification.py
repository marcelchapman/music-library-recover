"""Classify a missing folder by filename extensions without opening its files."""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path

AIFF_EXTENSIONS = {".aiff", ".aif"}
OTHER_AUDIO_EXTENSIONS = {
    ".aac", ".aifc", ".alac", ".ape", ".au", ".dff", ".dsd", ".dsf", ".flac", ".m4a",
    ".mp2", ".oga", ".ogg", ".opus", ".tak", ".tta", ".wav", ".wave", ".wma", ".wv",
}
NON_AUDIO_EXTENSIONS = {
    ".7z", ".avif", ".bmp", ".csv", ".cue", ".db", ".doc", ".docx", ".gif", ".heic", ".html", ".ico", ".ini",
    ".jpeg", ".jpg", ".json", ".log", ".m3u", ".m3u8", ".md", ".nfo", ".pdf", ".plist",
    ".pls", ".png", ".rar", ".rtf", ".svg", ".tif", ".tiff", ".txt", ".webp", ".xml", ".xspf", ".yaml", ".yml", ".zip",
}
NON_AUDIO_NAMES = {".ds_store", "readme", "license"}


class AudioFormat(Enum):
    MP3 = "MP3"
    AIFF = "AIFF"
    MIXED = "Mixed"
    NO_AUDIO = "No audio found"
    UNCLASSIFIED = "Unclassified"


@dataclass(frozen=True)
class Classification:
    audio_format: AudioFormat
    unknown_files: int = 0
    # A scan signature belongs to the recovery workflow, not the audio category.
    fingerprint: str = field(default="", compare=False, repr=False)


class ClassificationError(Exception):
    """A folder could not be inspected completely."""


def classify_folder(
    original: Path,
    relative_path: str,
    *,
    visit: Callable[[Path, os.stat_result], None] | None = None,
) -> Classification:
    """Inspect one independent missing directory and its descendants."""
    parts = relative_path.split("/")
    if any(part in ("", ".", "..") or Path(part).name != part for part in parts):
        raise ClassificationError("The folder path is invalid. Compare again.")

    formats: set[AudioFormat] = set()
    unknown_files = 0
    fingerprint = sha256()

    def record(path: Path, info: os.stat_result) -> None:
        relative = path.relative_to(tree)
        fingerprint.update(os.fsencode(relative))
        fingerprint.update(b"\0")
        fingerprint.update(repr(file_signature(info)).encode("ascii"))
        fingerprint.update(b"\n")
        if visit is not None:
            visit(relative, info)

    try:
        # Recheck each component below the selected root. A folder may have
        # moved or become a link since the directory comparison finished.
        folder = original
        _require_directory(folder)
        for part in parts[:-1]:
            folder = folder / part
            _require_directory(folder)

        tree = folder / parts[-1]
        pending = [tree]
        while pending:
            folder = pending.pop()
            record(folder, _require_directory(folder))
            with os.scandir(folder) as entries:
                for entry in sorted(entries, key=lambda entry: entry.name):
                    # is_file() can return False for a vanished entry. A stat
                    # lets us report that the scan was incomplete instead.
                    entry_path = folder / entry.name
                    info = entry_path.lstat()
                    if not stat.S_ISDIR(info.st_mode) or is_redirect(info):
                        record(entry_path, info)
                    if is_redirect(info):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(entry_path)
                    elif stat.S_ISREG(info.st_mode):
                        name = entry.name.casefold()
                        extension = Path(name).suffix
                        if name in NON_AUDIO_NAMES or name.startswith("._"):
                            continue
                        if extension == ".mp3":
                            formats.add(AudioFormat.MP3)
                        elif extension in AIFF_EXTENSIONS:
                            formats.add(AudioFormat.AIFF)
                        elif extension in OTHER_AUDIO_EXTENSIONS:
                            formats.add(AudioFormat.MIXED)
                        elif extension not in NON_AUDIO_EXTENSIONS:
                            unknown_files += 1
    except PermissionError as error:
        raise ClassificationError(
            "Access was denied. Check the folder’s permissions and compare again."
        ) from error
    except (FileNotFoundError, NotADirectoryError, ValueError) as error:
        raise ClassificationError("A file or folder became unavailable. Compare again.") from error
    except OSError as error:
        raise ClassificationError(
            "A file or folder couldn’t be checked. Verify the drive and compare again."
        ) from error

    if not formats:
        audio_format = AudioFormat.UNCLASSIFIED if unknown_files else AudioFormat.NO_AUDIO
    elif len(formats) == 1:
        audio_format = formats.pop()
    else:
        audio_format = AudioFormat.MIXED
    return Classification(audio_format, unknown_files, fingerprint.hexdigest())


def _require_directory(folder: Path) -> os.stat_result:
    info = folder.lstat()
    if is_redirect(info) or not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError
    return info


def is_redirect(info: os.stat_result) -> bool:
    # Windows junctions and other reparse points can redirect traversal.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def file_signature(info: os.stat_result) -> tuple[int, ...]:
    # Reading may update atime. Identity, type, size and change times are what
    # matter when comparing two observations of the same file.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
