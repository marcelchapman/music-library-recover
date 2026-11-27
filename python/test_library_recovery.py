import errno
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

import library_recovery as recovery
from audio_classification import AudioFormat, classify_folder
from library_recovery import RecoveryError, RecoveryItem, Status, prepare_recovery, recover


class LibraryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.original, self.current, self.output = (
            self.root / name for name in ("Original", "Current", "Recovery")
        )
        for folder in (self.original, self.current, self.output):
            folder.mkdir()

    def write(self, root, relative, contents):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        return target

    def item(self, path):
        return RecoveryItem(path, classify_folder(self.original, path))

    def plan(self, *items):
        return prepare_recovery(self.original, self.current, self.output, items)

    def assert_status(self, results, *statuses):
        self.assertEqual(tuple(result.status for result in results), statuses, results)

    def assert_no_staging(self):
        self.assertEqual(list(self.output.glob(".mlr-recovery-*")), [])

    def snapshot(self, root):
        return {
            path.relative_to(root): (
                path.stat().st_mode, path.stat().st_mtime_ns,
                path.read_bytes() if path.is_file() else None,
            )
            for path in (root, *root.rglob("*"))
        }

    def test_selected_formats_copy_full_trees_and_preserve_both_libraries(self):
        files = {
            "Atlas/Horizon/01.mp3": ("MP3", b"mp3 fixture"),
            "Atlas/Horizon/Cover.jpg": ("MP3", b"art fixture"),
            "Atlas/Horizon/Notes.txt": ("MP3", b"liner notes"),
            "Nova/Afterglow/Disc 1/Échos.AIF": ("AIFF", b"aiff fixture"),
            "Orion/Fragments/夜明け.mp3": ("MIXED", b"mp3 fixture"),
            "Orion/Fragments/02.aiff": ("MIXED", b"aiff fixture"),
        }
        for relative, (_, data) in files.items():
            self.write(self.original, relative, data)
        (self.original / "Nova/Empty booklet").mkdir()
        self.write(self.original, "Unselected/01.mp3", b"do not recover")
        self.write(self.current, "Atlas/Origins/01.mp3", b"current library")
        self.write(self.output, "MP3/Atlas/Pre-existing.txt", b"keep")
        self.write(self.output, "Existing.txt", b"keep also")
        original_before, current_before = self.snapshot(self.original), self.snapshot(self.current)

        plan = self.plan(*(self.item(path) for path in ("Atlas/Horizon", "Nova", "Orion")))
        self.assertEqual(plan.size, sum(len(data) for _, data in files.values()))
        self.assertEqual(plan.selected_count, 3)
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ["Existing.txt", "MP3"])

        self.assert_status(recover(plan), Status.RECOVERED, Status.RECOVERED, Status.RECOVERED)
        for relative, (category, data) in files.items():
            restored = self.output / category / relative
            self.assertEqual(restored.read_bytes(), data)
            self.assertEqual(restored.stat().st_mtime_ns, (self.original / relative).stat().st_mtime_ns)
        self.assertTrue((self.output / "AIFF/Nova/Empty booklet").is_dir())
        self.assertFalse((self.output / "MP3/Unselected").exists())
        self.assertFalse((self.output / "UNCLASSIFIED").exists())
        self.assertEqual((self.output / "MP3/Atlas/Pre-existing.txt").read_bytes(), b"keep")
        self.assertEqual((self.output / "Existing.txt").read_bytes(), b"keep also")
        self.assertEqual(self.snapshot(self.original), original_before)
        self.assertEqual(self.snapshot(self.current), current_before)
        self.assert_no_staging()

    def test_overlap_and_invalid_destinations(self):
        inside_original = self.original / "Inside"
        inside_current = self.current / "Inside"
        for path in (inside_original, inside_current):
            path.mkdir()
        regular_file = self.write(self.root, "Not a folder", b"x")
        for path in (self.original, self.current, self.root, inside_original, inside_current,
                     regular_file, self.root / "Missing"):
            with self.subTest(path=path.name), self.assertRaises(RecoveryError):
                prepare_recovery(self.original, self.current, path, [])

    def test_invalid_relative_paths_cannot_escape_destination(self):
        self.write(self.original, "Nova/01.mp3", b"x")
        classification = self.item("Nova").classification
        for path in ("../escape", "/absolute", "Nova//Album", "Nova/../Other"):
            with self.subTest(path=path):
                plan = self.plan(RecoveryItem(path, classification))
                self.assert_status(recover(plan), Status.SKIPPED)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_file_and_directory_conflicts(self):
        self.write(self.original, "Nova/01.mp3", b"new")
        self.write(self.original, "Orion/01.mp3", b"new")
        existing_dir = self.write(self.output, "MP3/Nova/Original.txt", b"existing dir")
        existing_file = self.write(self.output, "MP3/Orion", b"existing file")
        plan = self.plan(self.item("Nova"), self.item("Orion"))
        self.assertFalse(plan.folders)
        self.assert_status(recover(plan), Status.SKIPPED, Status.SKIPPED)
        self.assertEqual(existing_dir.read_bytes(), b"existing dir")
        self.assertEqual(existing_file.read_bytes(), b"existing file")

    def test_conflict_introduced_after_preparation(self):
        self.write(self.original, "Nova/01.mp3", b"new")
        plan = self.plan(self.item("Nova"))
        existing = self.write(self.output, "MP3/Nova", b"newly arrived")
        self.assert_status(recover(plan), Status.SKIPPED)
        self.assertEqual(existing.read_bytes(), b"newly arrived")
        self.assert_no_staging()

    def test_exclusive_publish_preserves_even_an_empty_racing_directory(self):
        self.write(self.original, "Nova/01.mp3", b"new")
        (self.output / "MP3").mkdir()
        plan = self.plan(self.item("Nova"))
        target_check = recovery._target
        occupied = None

        def race(*args, **kwargs):
            nonlocal occupied
            target = target_check(*args, **kwargs)
            if list(self.output.glob(".mlr-recovery-*")):
                target.mkdir()
                occupied = target.stat().st_ino
            return target

        with patch("library_recovery._target", side_effect=race):
            self.assert_status(recover(plan), Status.SKIPPED)
        target = self.output / "MP3/Nova"
        self.assertEqual(target.stat().st_ino, occupied)
        self.assertEqual(list(target.iterdir()), [])
        self.assert_no_staging()

    def make_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlinks are unavailable: {error}")

    def test_destination_redirections_are_rejected(self):
        outside = self.root / "Outside"
        outside.mkdir()
        self.make_link(self.root / "Alias", outside)
        self.make_link(self.output / "MP3", outside)
        self.write(self.original, "Nova/01.mp3", b"x")
        with self.assertRaises(RecoveryError):
            prepare_recovery(self.original, self.current, self.root / "Alias", [self.item("Nova")])
        self.assert_status(recover(self.plan(self.item("Nova"))), Status.SKIPPED)
        self.assertEqual(list(outside.iterdir()), [])

    def test_destination_redirect_introduced_during_transfer_is_skipped(self):
        outside = self.root / "Outside"
        outside.mkdir()
        self.write(self.original, "Nova/01.mp3", b"x")
        plan = self.plan(self.item("Nova"))
        self.make_link(self.output / "MP3", outside)
        self.assert_status(recover(plan), Status.SKIPPED)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor-based publication")
    def test_redirect_after_final_path_check_cannot_publish_outside_destination(self):
        outside = self.root / "Outside"
        outside.mkdir()
        self.write(self.original, "Artist/Nova/01.mp3", b"verified")
        category = self.output / "MP3"
        category.mkdir()
        plan = self.plan(self.item("Artist/Nova"))
        target_check = recovery._target

        def redirect(*args, **kwargs):
            target = target_check(*args, **kwargs)
            if list(self.output.glob(".mlr-recovery-*")):
                category.rmdir()
                self.make_link(category, outside)
            return target

        with patch("library_recovery._target", side_effect=redirect):
            self.assert_status(recover(plan), Status.SKIPPED)
        self.assertTrue(category.is_symlink())
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_no_staging()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux bind mount checks")
    def test_same_device_mount_boundaries_are_rejected(self):
        self.write(self.original, "Artist/Nova/01.mp3", b"source")
        category = self.output / "MP3"
        category.mkdir()
        item = self.item("Artist/Nova")
        self.assertEqual(category.stat().st_dev, (self.original / "Artist").stat().st_dev)
        for mount in (self.original / "Artist", self.original / "Artist/Nova", category):
            with self.subTest(mount=mount.name), patch.object(recovery, "_linux_mount_points", return_value={mount}):
                plan = self.plan(item)
                self.assertFalse(plan.folders)
                self.assert_status(recover(plan), Status.SKIPPED)
                self.assertIn("mounted", plan.skipped[0].detail)
        self.assertEqual(list(category.iterdir()), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux mountinfo format")
    def test_mountinfo_decodes_escaped_mount_points(self):
        sample = rb"52 37 8:1 /library /media/Space\040and\134slash rw - ext4 /dev/sda1 rw"
        with patch.object(Path, "read_bytes", return_value=sample):
            self.assertEqual(recovery._linux_mount_points(), {Path("/media/Space and\\slash")})

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux bind mount aliases")
    def test_destination_alias_of_library_descendant_is_rejected(self):
        device = b"8:1"
        for library in (self.original, self.current):
            mounts = (
                recovery.Mount(self.root, Path("/fixture"), device),
                recovery.Mount(self.output, Path("/fixture") / library.name / "Child", device),
            )
            with self.subTest(library=library.name), patch.object(recovery, "_linux_mounts", return_value=mounts):
                with self.assertRaisesRegex(RecoveryError, "separate from both libraries"):
                    self.plan()
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "POSIX directory modes")
    def test_publication_conflict_cleans_staged_read_only_directories(self):
        self.write(self.original, "Nova/Disc 1/01.mp3", b"verified")
        album = self.original / "Nova"
        disc = album / "Disc 1"
        abandoned = self.write(self.output, ".mlr-recovery-foreign/content/preserve", b"keep")
        self.addCleanup(album.chmod, 0o700)
        self.addCleanup(disc.chmod, 0o700)
        for directory in (album, disc):
            directory.chmod(0o555)
        plan = self.plan(self.item("Nova"))
        with patch.object(recovery, "_publish", side_effect=recovery.DestinationConflict("Late conflict")):
            self.assert_status(recover(plan), Status.SKIPPED)
        self.assertEqual(list(self.output.glob(".mlr-recovery-*")), [abandoned.parents[1]])
        self.assertEqual(abandoned.read_bytes(), b"keep")
        for directory in (album, disc):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o555)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux read-only directory rename")
    def test_read_only_album_is_published_with_original_modes(self):
        self.write(self.original, "Nova/Disc 1/01.mp3", b"verified")
        album = self.original / "Nova"
        for directory in (album, album / "Disc 1"):
            self.addCleanup(directory.chmod, 0o700)
            directory.chmod(0o555)
        plan = self.plan(self.item("Nova"))
        self.assert_status(recover(plan), Status.RECOVERED)
        restored = self.output / "MP3/Nova"
        for directory in (restored, restored / "Disc 1"):
            self.addCleanup(directory.chmod, 0o700)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o555)
        self.assertEqual((restored / "Disc 1/01.mp3").read_bytes(), b"verified")
        self.assert_no_staging()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux post-publication permissions")
    def test_permission_restore_failure_reports_published_folder(self):
        self.write(self.original, "Nova/01.mp3", b"verified")
        album = self.original / "Nova"
        self.addCleanup(album.chmod, 0o700)
        album.chmod(0o555)
        plan = self.plan(self.item("Nova"))
        with patch.object(recovery.os, "fchmod", side_effect=PermissionError(errno.EACCES, "injected")):
            results = recover(plan)
        self.assert_status(results, Status.RECOVERED)
        self.assertIn("restore", results[0].detail)
        self.assertEqual((self.output / "MP3/Nova/01.mp3").read_bytes(), b"verified")
        self.assert_no_staging()

    def test_nested_source_symlink_is_blocked(self):
        outside = self.root / "Outside"
        outside.mkdir()
        self.write(self.original, "Nova/01.mp3", b"x")
        self.make_link(self.original / "Nova/Loop", self.original)
        self.make_link(self.original / "Nova/External", outside)
        plan = self.plan(self.item("Nova"))
        self.assert_status(recover(plan), Status.SKIPPED)
        self.assertIn("link or special", plan.skipped[0].detail)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are unavailable")
    def test_special_source_item_is_blocked(self):
        self.write(self.original, "Nova/01.mp3", b"x")
        os.mkfifo(self.original / "Nova/Stream")
        self.assert_status(recover(self.plan(self.item("Nova"))), Status.SKIPPED)

    def test_insufficient_storage_blocks_preparation(self):
        self.write(self.original, "Nova/01.mp3", b"data")
        with patch("library_recovery.shutil.disk_usage") as usage:
            usage.return_value.free = 3
            with self.assertRaisesRegex(RecoveryError, "enough free space"):
                self.plan(self.item("Nova"))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_copy_io_errors_preserve_completed_output_and_clean_up(self):
        first = self.write(self.original, "Atlas/01.mp3", bytes(range(256)))
        second = self.write(self.original, "Nova/01.mp3", bytes(range(255, -1, -1)))
        unrelated = self.write(self.output, "Keep.txt", b"unrelated")
        plan = self.plan(self.item("Atlas"), self.item("Nova"))
        for failure in (errno.ENOSPC, errno.EACCES, errno.EIO):
            with self.subTest(errno=failure):
                with patch.object(recovery.os, "fsync", side_effect=[None, OSError(failure, "injected")]):
                    results = recover(plan)
                self.assert_status(results, Status.RECOVERED, Status.FAILED)
                if failure == errno.ENOSPC:
                    self.assertIn("ran out of space", results[1].detail)
                completed = self.output / "MP3/Atlas/01.mp3"
                self.assertEqual(completed.read_bytes(), first.read_bytes())
                self.assertFalse((self.output / "MP3/Nova").exists())
                self.assertEqual(second.read_bytes(), bytes(range(255, -1, -1)))
                self.assertEqual(unrelated.read_bytes(), b"unrelated")
                self.assert_no_staging()
                completed.unlink()
                completed.parent.rmdir()

    def test_source_read_denial_leaves_no_recovered_folder(self):
        source = self.write(self.original, "Nova/01.mp3", b"verified")
        plan = self.plan(self.item("Nova"))
        open_file = os.open

        def deny(path, *args, **kwargs):
            if path == source:
                raise PermissionError(errno.EACCES, "injected")
            return open_file(path, *args, **kwargs)

        with patch.object(recovery.os, "open", side_effect=deny):
            self.assert_status(recover(plan), Status.FAILED)
        self.assertEqual(source.read_bytes(), b"verified")
        self.assertFalse((self.output / "MP3/Nova").exists())
        self.assert_no_staging()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux exclusive rename")
    def test_unsupported_exclusive_rename_fails_without_fallback(self):
        self.write(self.original, "Nova/01.mp3", b"verified")
        plan = self.plan(self.item("Nova"))
        for code in (None, errno.ENOSYS, errno.ENOTSUP, errno.EINVAL):
            with self.subTest(code=code):
                with patch.object(recovery.ctypes, "CDLL") as libc, patch.object(recovery.os, "rename") as rename:
                    if code is None:
                        del libc.return_value.renameat2
                    else:
                        def unsupported(*args):
                            recovery.ctypes.set_errno(code)
                            return -1
                        libc.return_value.renameat2.side_effect = unsupported
                    results = recover(plan)
                    rename.assert_not_called()
                self.assert_status(results, Status.FAILED)
                self.assertIn("without replacement", results[0].detail)
                self.assertFalse((self.output / "MP3/Nova").exists())
                self.assert_no_staging()

    def test_unavailable_classification_is_ineligible_until_rescanned(self):
        self.write(self.original, "Archive/Track.xyz", b"unknown bytes")
        plan = self.plan(RecoveryItem("Archive", None))
        self.assertEqual(plan.selected_count, 1)
        self.assertEqual(plan.size, 0)
        self.assertFalse(plan.folders)
        self.assert_status(recover(plan), Status.SKIPPED)
        self.assertIn("Compare again", plan.skipped[0].detail)
        self.assertEqual(list(self.output.iterdir()), [])

        fresh_plan = self.plan(self.item("Archive"))
        self.assert_status(recover(fresh_plan), Status.RECOVERED)
        self.assertEqual(
            (self.output / "UNCLASSIFIED/Archive/Track.xyz").read_bytes(), b"unknown bytes"
        )

    def test_recovers_unknown_files_and_nested_structure(self):
        files = {
            "Atlas/Archive/Disc 1/Échos.xyz": b"unknown format",
            "Atlas/Archive/Artwork/Cover.jpg": b"associated artwork",
        }
        for relative, contents in files.items():
            self.write(self.original, relative, contents)
        (self.original / "Atlas/Archive/Empty booklet").mkdir()
        original_before = self.snapshot(self.original)
        item = self.item("Atlas/Archive")
        self.assertEqual(item.classification.audio_format, AudioFormat.UNCLASSIFIED)

        plan = self.plan(item)
        self.assertEqual(plan.selected_count, 1)
        self.assertEqual(plan.size, sum(map(len, files.values())))
        self.assertEqual(list(self.output.iterdir()), [])
        updates = []
        results = recover(plan, progress=lambda *args: updates.append(args))

        self.assert_status(results, Status.RECOVERED)
        self.assertEqual(results[0].path, "Atlas/Archive")
        self.assertEqual(updates, [(0, 1, "Atlas/Archive"), (1, 1, "")])
        for relative, contents in files.items():
            self.assertEqual((self.output / "UNCLASSIFIED" / relative).read_bytes(), contents)
        self.assertTrue((self.output / "UNCLASSIFIED/Atlas/Archive/Empty booklet").is_dir())
        self.assertEqual(self.snapshot(self.original), original_before)
        self.assert_no_staging()

    def test_recovers_known_non_audio_files(self):
        self.write(self.original, "Artwork/Scans/Cover.webp", b"image bytes")
        self.write(self.original, "Artwork/Notes.txt", b"notes")
        item = self.item("Artwork")
        self.assertEqual(item.classification.audio_format, AudioFormat.NO_AUDIO)

        self.assert_status(recover(self.plan(item)), Status.RECOVERED)
        target = self.output / "UNCLASSIFIED/Artwork"
        self.assertEqual((target / "Scans/Cover.webp").read_bytes(), b"image bytes")
        self.assertEqual((target / "Notes.txt").read_bytes(), b"notes")

    def test_recovers_empty_directories_with_zero_estimated_bytes(self):
        (self.original / "Atlas/Empty/Disc 1/Booklet").mkdir(parents=True)
        item = self.item("Atlas/Empty")
        self.assertEqual(item.classification.audio_format, AudioFormat.NO_AUDIO)
        plan = self.plan(item)
        self.assertEqual(plan.size, 0)
        self.assertFalse((self.output / "UNCLASSIFIED").exists())

        self.assert_status(recover(plan), Status.RECOVERED)
        target = self.output / "UNCLASSIFIED/Atlas/Empty/Disc 1/Booklet"
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assert_no_staging()

    def test_skips_existing_and_late_unclassified_conflicts(self):
        self.write(self.original, "Archive/Track.xyz", b"new bytes")
        self.write(self.original, "Booklet/Notes.txt", b"new notes")
        existing = self.write(self.output, "UNCLASSIFIED/Archive/Keep.xyz", b"existing bytes")
        plan = self.plan(self.item("Archive"), self.item("Booklet"))
        self.assert_status(plan.skipped, Status.SKIPPED)
        self.assertEqual(plan.size, len(b"new notes"))
        late = self.write(self.output, "UNCLASSIFIED/Booklet", b"late conflict")

        self.assert_status(recover(plan), Status.SKIPPED, Status.SKIPPED)
        self.assertEqual(existing.read_bytes(), b"existing bytes")
        self.assertEqual(late.read_bytes(), b"late conflict")
        self.assertFalse((existing.parent / "Track.xyz").exists())
        self.assert_no_staging()

    def test_unclassified_integrity_mismatch_prevents_publication(self):
        self.write(self.original, "Archive/Track.xyz", b"original bytes")
        plan = self.plan(self.item("Archive"))
        from hashlib import sha256

        with patch("library_recovery.sha256", side_effect=[sha256(), sha256(b"wrong bytes")]):
            results = recover(plan)
        self.assert_status(results, Status.FAILED)
        self.assertIn("integrity", results[0].detail)
        self.assertFalse((self.output / "UNCLASSIFIED").exists())
        self.assert_no_staging()

    def test_partial_copy_failure_never_publishes_payload(self):
        self.write(self.original, "Nova/01.mp3", b"original")
        plan = self.plan(self.item("Nova"))

        def fail(source, output, approved, cancelled):
            output.write_bytes(b"partial")
            raise OSError(errno.EIO, "simulated I/O failure")

        with patch("library_recovery._copy_file", side_effect=fail):
            self.assert_status(recover(plan), Status.FAILED)
        self.assertFalse((self.output / "MP3/Nova").exists())
        self.assert_no_staging()
        self.assertEqual((self.original / "Nova/01.mp3").read_bytes(), b"original")

    def test_mid_transfer_cancel_preserves_previously_completed_and_cleans_stage(self):
        for path in ("Atlas", "Nova", "Orion"):
            self.write(self.original, f"{path}/01.mp3", b"data")
        self.write(self.original, "Nova/01.mp3", b"\x9a" * (recovery.CHUNK_SIZE + 1))
        plan = self.plan(*(self.item(path) for path in ("Atlas", "Nova", "Orion")))
        stopped = Event()
        updates = []

        def cancelled():
            staged = self.output.glob(".mlr-recovery-*/content/01.mp3")
            if any(path.stat().st_size >= recovery.CHUNK_SIZE for path in staged):
                stopped.set()
            return stopped.is_set()

        results = recover(plan, cancelled=cancelled, progress=lambda *args: updates.append(args))
        self.assert_status(results, Status.RECOVERED, Status.CANCELLED, Status.CANCELLED)
        self.assertEqual((self.output / "MP3/Atlas/01.mp3").read_bytes(), b"data")
        self.assertFalse((self.output / "MP3/Nova").exists())
        self.assertFalse((self.output / "MP3/Orion").exists())
        self.assertEqual(updates[-1], (1, 3, ""))
        self.assert_no_staging()

    def test_cleanup_preserves_an_unrelated_replacement_for_the_stage(self):
        self.write(self.original, "Nova/01.mp3", b"verified")
        unrelated = self.write(self.output, "Unrelated/Keep.txt", b"keep")
        plan = self.plan(self.item("Nova"))
        displaced = self.output / "Displaced"
        replacement = None

        def replace_stage(destination, stage, relative):
            nonlocal replacement
            stage.path.rename(displaced)
            unrelated.parent.rename(stage.path)
            replacement = stage.path
            raise recovery.DestinationConflict("Late conflict")

        with patch.object(recovery, "_publish", side_effect=replace_stage):
            results = recover(plan)
        self.assert_status(results, Status.SKIPPED)
        self.assertIn("Temporary files may remain", results[0].detail)
        self.assertEqual((replacement / "Keep.txt").read_bytes(), b"keep")
        self.assertEqual((displaced / "content/01.mp3").read_bytes(), b"verified")
        self.assertFalse((self.output / "MP3/Nova").exists())

    def test_integrity_mismatch_prevents_publication(self):
        self.write(self.original, "Nova/01.mp3", b"original")
        from hashlib import sha256
        hash_in = sha256()
        hash_out = sha256(b"simulated digest mismatch")
        plan = self.plan(self.item("Nova"))
        with patch("library_recovery.sha256", side_effect=[hash_in, hash_out]):
            results = recover(plan)
        self.assert_status(results, Status.FAILED)
        self.assertIn("integrity", results[0].detail)
        self.assertFalse((self.output / "MP3/Nova").exists())
        self.assert_no_staging()

    def test_same_format_change_since_comparison_is_reported_in_preview(self):
        file = self.write(self.original, "Nova/01.mp3", b"before")
        found = self.item("Nova")
        file.write_bytes(b"after comparison")
        plan = self.plan(found)
        self.assert_status(recover(plan), Status.SKIPPED)
        self.assertIn("source contents or audio format changed", plan.skipped[0].detail)

    def test_format_change_after_preview_prevents_recovery(self):
        self.write(self.original, "Nova/01.mp3", b"mp3")
        plan = self.plan(self.item("Nova"))
        self.write(self.original, "Nova/02.aif", b"aif")
        self.assert_status(recover(plan), Status.FAILED)
        self.assertFalse((self.output / "MP3/Nova").exists())

    def test_source_change_during_copy_prevents_recovery(self):
        file = self.write(self.original, "Nova/01.mp3", b"mp3")
        plan = self.plan(self.item("Nova"))
        copy = recovery._copy_file

        def mutate(*args):
            copy(*args)
            file.write_bytes(b"replacement")

        with patch("library_recovery._copy_file", side_effect=mutate):
            self.assert_status(recover(plan), Status.FAILED)
        self.assertFalse((self.output / "MP3/Nova").exists())
        self.assert_no_staging()

    def test_source_removal_after_copy_prevents_publication(self):
        source = self.write(self.original, "Nova/01.mp3", b"verified")
        plan = self.plan(self.item("Nova"))
        copy = recovery._copy_file

        def remove(*args):
            copy(*args)
            source.unlink()

        with patch.object(recovery, "_copy_file", side_effect=remove):
            self.assert_status(recover(plan), Status.FAILED)
        self.assertFalse((self.output / "MP3/Nova").exists())
        self.assert_no_staging()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Native Linux publication and restart")
    def test_process_exit_leaves_only_its_stage_and_next_run_preserves_it(self):
        first = self.write(self.original, "Atlas/01.mp3", b"first")
        second = self.write(self.original, "Nova/01.mp3", b"second")
        unrelated = self.write(self.output, ".mlr-recovery-unrelated/Keep", b"keep")
        script = """
import os
import sys
from pathlib import Path
from audio_classification import classify_folder
import library_recovery as r

original, current, destination = map(Path, sys.argv[1:])
plan = r.prepare_recovery(original, current, destination, [
    r.RecoveryItem(name, classify_folder(original, name)) for name in ("Atlas", "Nova")
])
copy = r._copy_file

def interrupt(source, output, approved, cancelled):
    if source.parent.name == "Nova":
        output.write_bytes(b"partial")
        os._exit(23)
    copy(source, output, approved, cancelled)

r._copy_file = interrupt
r.recover(plan)
"""
        child = subprocess.run(
            [sys.executable, "-c", script, *map(str, (self.original, self.current, self.output))],
            cwd=Path(recovery.__file__).parent, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(child.returncode, 23, child.stderr)
        (abandoned,) = set(self.output.glob(".mlr-recovery-*")) - {unrelated.parent}
        partial = abandoned / "content/01.mp3"
        self.assertEqual(partial.read_bytes(), b"partial")
        completed = self.output / "MP3/Atlas/01.mp3"
        self.assertEqual(completed.read_bytes(), first.read_bytes())
        completed_identity = completed.stat().st_ino
        self.assertFalse((self.output / "MP3/Nova").exists())

        self.assert_status(recover(self.plan(self.item("Atlas"), self.item("Nova"))),
                           Status.SKIPPED, Status.RECOVERED)
        self.assertEqual((self.output / "MP3/Nova/01.mp3").read_bytes(), second.read_bytes())
        self.assertEqual(completed.stat().st_ino, completed_identity)
        self.assertEqual(completed.read_bytes(), first.read_bytes())
        self.assertEqual(partial.read_bytes(), b"partial")
        self.assertEqual(unrelated.read_bytes(), b"keep")
        self.assertEqual(set(self.output.glob(".mlr-recovery-*")), {abandoned, unrelated.parent})

    def test_folder_added_to_current_library_after_comparison_is_skipped(self):
        self.write(self.original, "Nova/01.mp3", b"mp3")
        found = self.item("Nova")
        (self.current / "Nova").mkdir()
        plan = self.plan(found)
        self.assert_status(recover(plan), Status.SKIPPED)
        self.assertIn("current library", plan.skipped[0].detail)

    def test_cancelled_preview_writes_nothing(self):
        self.write(self.original, "Nova/01.mp3", b"x")
        with self.assertRaises(recovery.RecoveryCancelled):
            prepare_recovery(self.original, self.current, self.output, [self.item("Nova")], cancelled=lambda: True)
        self.assertEqual(list(self.output.iterdir()), [])
