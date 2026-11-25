import errno
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from library_comparison import ComparisonError, compare_libraries


class CompareLibrariesTests(unittest.TestCase):
    def setUp(self):
        workspace = self.enterContext(TemporaryDirectory())
        self.original = Path(workspace) / "Original"
        self.current = Path(workspace) / "Current"
        self.original.mkdir()
        self.current.mkdir()

    def folders(self, root, *names):
        for name in names:
            (root / name).mkdir(parents=True, exist_ok=True)

    def compare(self):
        return compare_libraries(self.original, self.current)

    def test_identical_directory_trees(self):
        for library in (self.original, self.current):
            self.folders(library, "Atlas/Origins", "Nova/Afterglow")
        self.assertEqual(self.compare().missing, ())

    def test_missing_album_preserves_artist_path(self):
        self.folders(self.original, "Atlas/Origins", "Atlas/Horizon")
        self.folders(self.current, "Atlas/Origins")
        self.assertEqual(self.compare().missing, ("Atlas/Horizon",))

    def test_missing_artists_cover_all_descendants(self):
        self.folders(self.original, "Nova/Afterglow/Disc 1", "Nova/Arc", "Orion/Fragments")
        self.assertEqual(self.compare().missing, ("Nova", "Orion"))

    def test_first_missing_folder_in_each_nested_branch(self):
        self.folders(self.original, "Jazz/Atlas/Origins/Disc 1", "Jazz/Atlas/Horizon/Disc 2")
        self.folders(self.current, "Jazz/Atlas/Origins")
        self.assertEqual(
            self.compare().missing,
            ("Jazz/Atlas/Horizon", "Jazz/Atlas/Origins/Disc 1"),
        )

    def test_album_names_are_scoped_to_their_relative_parent(self):
        self.folders(self.original, "Artist A/Greatest Hits", "Artist B/Greatest Hits")
        self.folders(self.current, "Artist A/Greatest Hits", "Artist B")
        self.assertEqual(self.compare().missing, ("Artist B/Greatest Hits",))

    def test_empty_libraries(self):
        self.assertEqual(self.compare().missing, ())

    def test_current_only_folders_are_ignored(self):
        self.folders(self.current, "Atlas/Origins")
        self.assertEqual(self.compare().missing, ())

    def test_files_do_not_count_as_folders(self):
        self.folders(self.original, "Atlas/Origins")
        self.folders(self.current, "Atlas")
        (self.current / "Atlas" / "Origins").write_bytes(b"fictional file")
        (self.original / "Atlas" / "Demo.mp3").write_bytes(b"fictional file")
        self.assertEqual(self.compare().missing, ("Atlas/Origins",))

    def test_invalid_library_paths(self):
        regular_file = self.original / "Track.mp3"
        regular_file.touch()
        for path in (self.current / "Absent", regular_file, Path("bad\0path")):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ComparisonError, "current library"):
                    compare_libraries(self.original, path)
        with self.assertRaisesRegex(ComparisonError, "original library"):
            compare_libraries(self.current / "Absent", self.current)

    def test_spaces_and_unicode(self):
        self.folders(self.original, "Sølar Drift/Échos du soir")
        self.folders(self.current, "Sølar Drift")
        self.assertEqual(self.compare().missing, ("Sølar Drift/Échos du soir",))

    def test_access_error_in_either_library_aborts_comparison(self):
        self.folders(self.original, "Atlas/Origins")
        self.folders(self.current, "Atlas/Origins")
        scandir = os.scandir

        for library, role in ((self.original, "original"), (self.current, "current")):
            blocked = library / "Atlas"

            def scan(path):
                if path == blocked:
                    raise PermissionError(errno.EACCES, "test access error")
                return scandir(path)

            with self.subTest(library=role), patch("library_comparison.os.scandir", scan):
                with self.assertRaisesRegex(ComparisonError, f"Access was denied.*Atlas.*{role}"):
                    self.compare()

    def test_filesystem_error_aborts_comparison(self):
        with patch("library_comparison.os.scandir", side_effect=OSError(errno.EIO, "test I/O error")):
            with self.assertRaisesRegex(ComparisonError, "Couldn’t read the original library"):
                self.compare()

    def symlink(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"Could not create a directory symlink: {error}")

    def test_links_are_not_traversed_or_matched_as_folders(self):
        outside = Path(self.enterContext(TemporaryDirectory()))
        self.folders(outside, "Do not scan")
        self.folders(self.original, "Atlas/Origins")
        self.folders(self.current, "Atlas")
        self.symlink(self.original / "Outside", outside)
        self.symlink(self.original / "Broken", outside / "Absent")
        self.symlink(self.original / "Atlas" / "Loop", self.original)
        self.symlink(self.current / "Atlas" / "Origins", outside)

        result = self.compare()
        self.assertEqual(result.missing, ("Atlas/Origins",))
        self.assertEqual(result.skipped_links, 4)

    def test_link_as_library_root_is_rejected(self):
        alias = self.current.parent / "Alias"
        self.symlink(alias, self.original)
        with self.assertRaisesRegex(ComparisonError, "real folder.*original library"):
            compare_libraries(alias, self.current)

    def test_comparison_leaves_both_library_trees_intact(self):
        for library in (self.original, self.current):
            self.folders(library, "Atlas/Origins")
            (library / "Atlas" / "Origins" / "Track.mp3").write_bytes(b"fictional audio")
        self.folders(self.original, "Nova/Afterglow")

        def snapshot(root):
            entries = {}
            for path in (root, *root.rglob("*")):
                metadata = path.stat()
                entries[path.relative_to(root)] = (
                    metadata.st_mode,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    path.read_bytes() if path.is_file() else None,
                )
            return entries

        before = (snapshot(self.original), snapshot(self.current))
        self.assertEqual(self.compare().missing, ("Nova",))
        self.assertEqual((snapshot(self.original), snapshot(self.current)), before)


if __name__ == "__main__":
    unittest.main()
