import errno
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from audio_classification import AudioFormat, Classification, ClassificationError, classify_folder
from library_comparison import compare_libraries


class AudioClassificationTests(unittest.TestCase):
    def setUp(self):
        self.original = Path(self.enterContext(TemporaryDirectory()))
        self.folder = self.original / "Nova"
        self.folder.mkdir()

    def add_files(self, *names):
        for name in names:
            file = self.folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()

    def classify(self):
        return classify_folder(self.original, "Nova")

    def test_mp3_only(self):
        self.add_files("01.mp3", "02.mp3")
        self.assertEqual(self.classify(), Classification(AudioFormat.MP3))

    def test_aiff_only(self):
        self.add_files("01.aiff")
        self.assertEqual(self.classify(), Classification(AudioFormat.AIFF))

    def test_aif_only(self):
        self.add_files("01.aif")
        self.assertEqual(self.classify(), Classification(AudioFormat.AIFF))

    def test_mp3_and_aiff_are_mixed(self):
        self.add_files("01.mp3", "02.aiff")
        self.assertEqual(self.classify(), Classification(AudioFormat.MIXED))

    def test_other_recognised_audio_formats_are_mixed_on_their_own(self):
        for extension in ("flac", "wav", "m4a", "aac", "ogg", "opus", "alac", "wma", "dsd", "dsf", "dff"):
            with self.subTest(extension=extension):
                filename = self.folder / f"Track.{extension}"
                filename.touch()
                self.assertEqual(self.classify(), Classification(AudioFormat.MIXED))
                filename.unlink()

    def test_other_audio_and_mp3_are_mixed(self):
        self.add_files("01.mp3", "02.flac")
        self.assertEqual(self.classify(), Classification(AudioFormat.MIXED))

    def test_extensions_are_case_insensitive(self):
        self.add_files("01.Mp3", "02.MP3")
        self.assertEqual(self.classify(), Classification(AudioFormat.MP3))

    def test_independent_missing_parent_uses_all_descendant_albums(self):
        self.add_files("Afterglow/01.aif", "Archives/Nightfall/01.AIFF")
        current = Path(self.enterContext(TemporaryDirectory()))
        result = compare_libraries(self.original, current)
        self.assertEqual(result.missing, ("Nova",))
        self.assertEqual(classify_folder(self.original, result.missing[0]), Classification(AudioFormat.AIFF))

    def test_artwork_documents_playlists_and_appledouble_are_ignored(self):
        self.add_files("01.MP3", "cover.JPEG", "notes.pdf", "disc.m3u8", ".DS_Store", "._01.aif")
        self.assertEqual(self.classify(), Classification(AudioFormat.MP3))

    def test_empty_folder(self):
        (self.folder / "Empty album").mkdir()
        self.assertEqual(self.classify(), Classification(AudioFormat.NO_AUDIO))

    def test_only_known_non_audio_files(self):
        self.add_files("cover.webp", "notes.txt", "list.pls")
        self.assertEqual(self.classify(), Classification(AudioFormat.NO_AUDIO))

    def test_only_unknown_files(self):
        self.add_files("archive.unknown", "untitled")
        self.assertEqual(self.classify(), Classification(AudioFormat.UNCLASSIFIED, unknown_files=2))

    def test_unknown_files_are_distinct_from_recognised_audio(self):
        self.add_files("01.mp3", "extras.unknown")
        self.assertEqual(self.classify(), Classification(AudioFormat.MP3, unknown_files=1))

    def test_unicode_paths(self):
        self.add_files("Échos du soir/夜明け.AiF")
        self.assertEqual(
            classify_folder(self.original, "Nova/Échos du soir"), Classification(AudioFormat.AIFF)
        )

    def symlink(self, link, target, is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=is_directory)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"Could not create a symbolic link: {error}")

    def test_does_not_follow_links_to_files_or_directories(self):
        outside = Path(self.enterContext(TemporaryDirectory()))
        (outside / "Track.flac").touch()
        self.add_files("01.mp3")
        self.symlink(self.folder / "Loop", self.folder, is_directory=True)
        self.symlink(self.folder / "External", outside, is_directory=True)
        self.symlink(self.folder / "Link.flac", outside / "Track.flac")
        self.symlink(self.folder / "Broken.flac", outside / "Absent")
        self.assertEqual(self.classify(), Classification(AudioFormat.MP3))

    def test_refuses_a_link_in_the_missing_path(self):
        self.add_files("Afterglow/01.mp3")
        self.symlink(self.original / "Alias", self.folder, is_directory=True)
        with self.assertRaises(ClassificationError):
            classify_folder(self.original, "Alias/Afterglow")

    def test_invalid_relative_paths(self):
        for path in ("", "/Nova", "../Nova", "Nova//Album"):
            with self.subTest(path=path), self.assertRaises(ClassificationError):
                classify_folder(self.original, path)

    def test_inaccessible_subfolder_invalidates_classification(self):
        self.add_files("01.mp3", "Afterglow/01.aiff")
        scan = os.scandir

        def guarded(folder):
            if folder == self.folder / "Afterglow":
                raise PermissionError(errno.EACCES, "test access error")
            return scan(folder)

        with patch("audio_classification.os.scandir", side_effect=guarded):
            with self.assertRaisesRegex(ClassificationError, "Access was denied"):
                self.classify()

    def test_file_disappearing_during_the_scan_invalidates_classification(self):
        vanished = Mock()
        vanished.name = "vanished.mp3"
        with patch("audio_classification.os.scandir") as scan:
            scan.return_value.__enter__.return_value = [vanished]
            with self.assertRaisesRegex(ClassificationError, "became unavailable"):
                self.classify()

    def test_preserves_tree_and_file_contents(self):
        self.add_files("Afterglow/Track.aiff", "cover.jpg")
        track = self.folder / "Afterglow" / "Track.aiff"
        track.write_bytes(b"fictional bytes, not audio")

        def snapshot():
            return {
                path.relative_to(self.original): (path.stat().st_mtime_ns, path.stat().st_mode,
                                                  path.read_bytes() if path.is_file() else None)
                for path in (self.original, *self.original.rglob("*"))
            }

        before = snapshot()
        self.assertEqual(self.classify(), Classification(AudioFormat.AIFF))
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
