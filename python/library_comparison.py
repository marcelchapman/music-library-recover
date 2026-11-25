"""Read-only comparison of two directory trees."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

DirectoryParts = tuple[str, ...]


class ComparisonError(Exception):
    """A library couldn't be scanned completely."""


@dataclass(frozen=True)
class ComparisonResult:
    missing: tuple[str, ...]
    skipped_links: int


def compare_libraries(original: Path, current: Path) -> ComparisonResult:
    """Return independent missing folder paths, relative to the original."""
    original_paths, original_links = _scan(Path(original), "original")
    current_paths, current_links = _scan(Path(current), "current")

    # Every scanned child's parent is in the same tree. A missing child is
    # independent only if it is at the root or its parent exists in the current tree.
    missing = (
        "/".join(parts)
        for parts in original_paths - current_paths
        if len(parts) == 1 or parts[:-1] in current_paths
    )
    return ComparisonResult(
        tuple(sorted(missing, key=lambda path: (path.casefold(), path))),
        original_links + current_links,
    )


def _scan(root: Path, role: str) -> tuple[set[DirectoryParts], int]:
    directories: set[DirectoryParts] = set()
    pending: list[DirectoryParts] = [()]
    skipped_links = 0

    while pending:
        parts = pending.pop()
        folder = root.joinpath(*parts)
        location = f"the {role} library"
        if parts:
            location = f"“{'/'.join(parts)}” in the {role} library"

        try:
            # Check again before entering a queued directory. Windows junctions
            # and other reparse points can redirect traversal just like symlinks.
            info = folder.lstat()
            if _is_link(info):
                if parts:
                    skipped_links += 1
                    continue
                raise ComparisonError(f"Choose a real folder for the {role} library.")
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError

            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_symlink() or (
                        os.name == "nt" and _is_link(entry.stat(follow_symlinks=False))
                    ):
                        skipped_links += 1
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append((*parts, entry.name))
            if parts:
                directories.add(parts)
        except PermissionError as error:
            raise ComparisonError(
                f"Access was denied while reading {location}. Check its permissions and try again."
            ) from error
        except (FileNotFoundError, NotADirectoryError, ValueError) as error:
            raise ComparisonError(
                f"Couldn’t find a folder at {location}. Check your selection and try again."
            ) from error
        except OSError as error:
            raise ComparisonError(
                f"Couldn’t read {location}. Check that the drive is connected and try again."
            ) from error

    return directories, skipped_links


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
