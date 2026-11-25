"""Music Library Recovery desktop entry point."""

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, QThread, Signal, Slot
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from library_comparison import ComparisonError, ComparisonResult, compare_libraries

TITLE = "Music Library Recovery"
EMBLEM = Path(__file__).resolve().parent.parent / "assets" / "emblem.png"

STYLESHEET = """
QWidget#window { background: #181c21; color: #eff2f4; }
QLabel { color: #eff2f4; }
QLabel#wordmark { color: #c1cbd2; font-size: 13px; font-weight: 600; }
QLabel#title { font-size: 32px; font-weight: 650; }
QLabel#subtitle, QLabel#status, QLabel#empty, QLabel#column {
    color: #aebbc4;
}
QLabel#status, QLabel#column { font-size: 13px; }
QLabel#sectionTitle { font-size: 18px; font-weight: 650; }
QLabel#count {
    color: #9fe3ec;
    background: #233941;
    border-radius: 5px;
    padding: 3px 8px;
    font-weight: 600;
}
QFrame#surface { background: #20262c; border: 1px solid #39434c; border-radius: 8px; }
QFrame#divider { background: #39434c; }
QLineEdit {
    color: #c5cfd6;
    background: #171d22;
    border: 1px solid #48555e;
    border-radius: 5px;
    padding: 0 10px;
    selection-color: #112027;
    selection-background-color: #8bdce9;
}
QLineEdit:focus, QListView:focus { border: 1px solid #86dfec; }
QPushButton {
    background: #2b343c;
    color: #ebf0f3;
    border: 1px solid #54626c;
    border-radius: 5px;
    padding: 0 14px;
    font-weight: 600;
}
QPushButton:hover { background: #374550; }
QPushButton:pressed { background: #242d35; }
QPushButton:focus { border: 2px solid #9fe9f3; padding: 0 13px; }
QPushButton:disabled { color: #a7b4be; background: #252d34; }
QPushButton#compare { background: #93dce8; color: #112128; border-color: #93dce8; }
QPushButton#compare:hover { background: #b1eaf2; }
QPushButton#compare:pressed { background: #77c4d2; }
QPushButton#compare:focus { border-color: #f1fbfd; }
QPushButton#compare:disabled { background: #2d454e; color: #b3ccd3; border-color: #425c67; }
QListView {
    background: #1a2026;
    color: #e4ecef;
    border: 1px solid #39434c;
    border-radius: 5px;
    outline: 0;
    alternate-background-color: #20272e;
}
QListView::item { padding: 8px 12px; }
QListView::item:selected { background: #314d59; color: #f2f8fa; }
QProgressBar { background: #2b3840; border: 0; border-radius: 2px; }
QProgressBar::chunk { background: #93dce8; border-radius: 2px; }
"""


def label(text: str, style: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(style)
    return widget


class ComparisonThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, original: Path, current: Path, parent: QWidget):
        super().__init__(parent)
        self.original = original
        self.current = current

    def run(self) -> None:
        try:
            result = compare_libraries(self.original, self.current)
        except ComparisonError as error:
            self.failed.emit(str(error))
        except Exception:
            logging.error("Unexpected error during library comparison.")
            self.failed.emit("Something interrupted the comparison. Try again.")
        else:
            self.succeeded.emit(result)


class LibraryPicker(QWidget):
    changed = Signal()

    def __init__(self, title: str, description: str):
        super().__init__()
        self.path: Path | None = None

        heading = label(title)
        heading.setStyleSheet("font-weight: 650;")
        hint = label(description, "status")
        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("No folder selected")
        self.path_field.setAccessibleName(title + " folder")
        self.path_field.setMinimumHeight(44)
        self.choose_button = QPushButton("Choose folder…")
        self.choose_button.setAccessibleName("Choose " + title.lower() + " folder")
        self.choose_button.setMinimumHeight(44)
        self.choose_button.clicked.connect(self.choose)
        self.dialog_title = "Choose " + title.lower()

        fields = QHBoxLayout()
        fields.setSpacing(10)
        fields.addWidget(self.path_field, 1)
        fields.addWidget(self.choose_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addSpacing(4)
        layout.addLayout(fields)

    @Slot()
    def choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            self.dialog_title,
            str(self.path) if self.path is not None else "",
            QFileDialog.Option.ShowDirsOnly
            | QFileDialog.Option.ReadOnly
            | QFileDialog.Option.DontResolveSymlinks,
        )
        if folder:
            self.path = Path(folder)
            self.path_field.setText(str(self.path))
            self.path_field.setToolTip(str(self.path))
            self.changed.emit()


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("window")
        self.setWindowTitle(TITLE)
        self.setWindowIcon(QIcon(str(EMBLEM)))
        self.setMinimumSize(560, 640)
        self.resize(780, 712)
        self.thread: ComparisonThread | None = None
        self.close_after_scan = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 28)
        layout.setSpacing(0)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(32, 32))
        brand.addWidget(icon)
        brand.addWidget(label(TITLE, "wordmark"), 1)
        layout.addLayout(brand)
        layout.addSpacing(16)
        layout.addWidget(label("Compare libraries", "title"))
        subtitle = label(
            "Find folders from your original library that are missing in your current library.",
            "subtitle",
        )
        subtitle.setWordWrap(True)
        layout.addSpacing(4)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        libraries = QFrame(objectName="surface")
        library_layout = QVBoxLayout(libraries)
        library_layout.setContentsMargins(16, 12, 16, 12)
        library_layout.setSpacing(10)
        self.original = LibraryPicker("Original library", "The collection you started with.")
        self.current = LibraryPicker("Current library", "The collection you want to check.")
        divider = QFrame(objectName="divider")
        divider.setFixedHeight(1)
        library_layout.addWidget(self.original)
        library_layout.addWidget(divider)
        library_layout.addWidget(self.current)
        layout.addWidget(libraries)
        layout.addSpacing(12)

        action = QHBoxLayout()
        action.setSpacing(12)
        self.status = label("", "status")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Scanning both libraries")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(64, 4)
        self.progress.hide()
        self.compare_button = QPushButton("Compare libraries", objectName="compare")
        self.compare_button.setMinimumHeight(44)
        action.addWidget(self.status, 1)
        action.addWidget(self.progress)
        action.addWidget(self.compare_button)
        layout.addLayout(action)
        layout.addSpacing(14)

        results = QFrame(objectName="surface")
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(16, 12, 16, 14)
        results_layout.setSpacing(8)
        results_heading = QHBoxLayout()
        results_heading.addWidget(label("Missing folders", "sectionTitle"))
        results_heading.addStretch()
        self.count = label("", "count")
        results_heading.addWidget(self.count)
        results_layout.addLayout(results_heading)

        self.result_pages = QStackedWidget()
        self.empty = label("", "empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.result_pages.addWidget(self.empty)

        paths_page = QWidget()
        paths_layout = QVBoxLayout(paths_page)
        paths_layout.setContentsMargins(0, 0, 0, 0)
        paths_layout.setSpacing(5)
        paths_layout.addWidget(label("Path in the original library", "column"))
        self.paths = QListView()
        self.paths.setAccessibleName("Missing paths in the original library")
        self.paths.setAlternatingRowColors(True)
        self.paths.setUniformItemSizes(True)
        self.paths.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.paths.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.paths.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.path_model = QStringListModel(self)
        self.paths.setModel(self.path_model)
        self.paths.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        paths_layout.addWidget(self.paths)
        self.result_pages.addWidget(paths_page)
        results_layout.addWidget(self.result_pages, 1)
        layout.addWidget(results, 1)

        self.original.changed.connect(self.reset_results)
        self.current.changed.connect(self.reset_results)
        self.compare_button.clicked.connect(self.start_comparison)
        self.reset_results()

    @Slot()
    def reset_results(self) -> None:
        ready = self.original.path is not None and self.current.path is not None
        self.compare_button.setEnabled(ready)
        self.status.setText("Ready to compare." if ready else "Choose both library folders to begin.")
        self.show_message("Your comparison results will appear here.")

    def show_message(self, text: str, error: bool = False) -> None:
        self.path_model.setStringList([])
        self.count.hide()
        self.empty.setStyleSheet("color: #ffb3a5;" if error else "")
        self.empty.setText(text)
        self.result_pages.setCurrentIndex(0)

    @Slot()
    def start_comparison(self) -> None:
        original, current = self.original.path, self.current.path
        if original is None or current is None or self.thread is not None:
            return

        self.show_message("Looking for missing folders…")
        self.status.setText("Scanning both libraries…")
        self.progress.show()
        self.original.choose_button.setEnabled(False)
        self.current.choose_button.setEnabled(False)
        self.compare_button.setEnabled(False)

        # Only the worker reads the filesystem. Signals return results to the UI thread.
        self.thread = ComparisonThread(original, current, self)
        self.thread.succeeded.connect(self.show_result)
        self.thread.failed.connect(self.show_error)
        self.thread.finished.connect(self.finish_comparison)
        self.thread.start()

    @Slot(object)
    def show_result(self, result: ComparisonResult) -> None:
        if result.missing:
            self.path_model.setStringList(list(result.missing))
            self.result_pages.setCurrentIndex(1)
        else:
            self.show_message("No missing folders found.")
        self.count.setText(f"{len(result.missing):,}")
        self.count.setAccessibleName(f"{len(result.missing):,} missing folders")
        self.count.show()

        status = "Comparison complete."
        if result.skipped_links == 1:
            status += " 1 link skipped."
        elif result.skipped_links:
            status += f" {result.skipped_links:,} links skipped."
        self.status.setText(status)

    @Slot(str)
    def show_error(self, text: str) -> None:
        self.show_message(text, error=True)
        self.status.setText("Comparison couldn’t finish.")

    @Slot()
    def finish_comparison(self) -> None:
        if self.thread is not None:
            self.thread.deleteLater()
            self.thread = None
        self.progress.hide()
        self.original.choose_button.setEnabled(True)
        self.current.choose_button.setEnabled(True)
        self.compare_button.setEnabled(True)
        if self.close_after_scan:
            self.close()

    def closeEvent(self, event) -> None:
        if self.thread is not None:
            self.close_after_scan = True
            self.status.setText("Closing when the comparison finishes…")
            event.ignore()
            return
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    app.setWindowIcon(QIcon(str(EMBLEM)))
    app.setStyle("Fusion")

    # Prefer the project's typefaces if installed, otherwise keep the system UI font.
    installed = QFontDatabase.families()
    for family in ("Inter", "Geist Sans", "Geist"):
        if family in installed:
            font = QFont(app.font())
            font.setFamily(family)
            app.setFont(font)
            break

    window = Window()
    window.setStyleSheet(STYLESHEET)
    window.show()
    exit_code = app.exec()
    if window.thread is not None:
        window.thread.wait()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
