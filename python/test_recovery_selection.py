import unittest
from importlib.util import find_spec

from audio_classification import AudioFormat, Classification


@unittest.skipUnless(find_spec("PySide6"), "Qt is needed to test the recovery selection model")
class RecoverySelectionTests(unittest.TestCase):
    def test_select_all_includes_unclassified_and_preserves_eligibility(self):
        from PySide6.QtCore import Qt

        from app import FolderPaths, MissingFolder

        model = FolderPaths(None)
        model.set_folders([
            MissingFolder("Horizon", Classification(AudioFormat.MP3)),
            MissingFolder("Archive", Classification(AudioFormat.UNCLASSIFIED, unknown_files=1)),
            MissingFolder("Artwork", Classification(AudioFormat.NO_AUDIO)),
            MissingFolder("Unavailable", None, "Compare again."),
        ])
        archive, artwork, unavailable = (model.index(row) for row in range(1, 4))
        check_role = Qt.ItemDataRole.CheckStateRole
        checked = Qt.CheckState.Checked

        self.assertEqual(model.checked_rows, set())
        self.assertTrue(model.setData(archive, checked, check_role))
        self.assertTrue(model.setData(artwork, checked, check_role))
        self.assertFalse(model.flags(unavailable) & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertFalse(model.setData(unavailable, checked, check_role))
        self.assertEqual(model.checked_rows, {1, 2})

        model.toggle_all()
        self.assertEqual(model.checked_rows, {0, 1, 2})
        self.assertEqual(archive.data(Qt.ItemDataRole.AccessibleTextRole), "Archive, Unclassified")
        self.assertEqual(artwork.data(Qt.ItemDataRole.AccessibleTextRole), "Artwork, No audio found")
        self.assertEqual(unavailable.data(Qt.ItemDataRole.AccessibleTextRole), "Unavailable, Format unavailable")

        model.toggle_all()
        self.assertEqual(model.checked_rows, set())
