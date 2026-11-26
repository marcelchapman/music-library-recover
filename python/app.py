"""Music Library Recovery desktop entry point."""

import logging
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from audio_classification import Classification, ClassificationError, classify_folder
from library_comparison import ComparisonError, ComparisonResult, compare_libraries

TITLE = "Music Library Recovery"
EMBLEM = Path(__file__).resolve().parent.parent / "assets" / "emblem.png"

GROUND = "#181a1d"
SURFACE = "#212428"
RULE = "#303538"
INK = "#f2f0eb"
SECONDARY = "#b7bec2"
ACCENT = "#90cbd2"
FOCUS = "#a5e3eb"
WARNING = "#f2b2a4"

STYLESHEET = """
QWidget#window { background: @ground; color: @ink; }
QWidget#libraries { background: @surface; border-radius: 6px; }
QFrame#divider { background: @rule; }
QLabel { color: @ink; }
QLabel#title { font-size: 30px; font-weight: 550; }
QLabel#subtitle { font-size: 15px; color: @secondary; }
QLabel#sectionTitle { font-size: 20px; font-weight: 550; }
QLabel#libraryLabel, QLabel#status, QLabel#count, QLabel#hint, QLabel#detail {
    font-size: 13px;
    color: @secondary;
}
QLabel#stateTitle { font-size: 15px; font-weight: 500; }
QPushButton {
    font-size: 14px;
    font-weight: 500;
    color: @ink;
    background: transparent;
    border: 2px solid transparent;
    border-radius: 5px;
    padding: 0 14px;
}
QPushButton:hover { background: #2c3136; }
QPushButton:pressed { background: #343b40; }
QPushButton:disabled { color: #969ea3; }
QPushButton#compare { background: @accent; color: #132528; padding: 0 19px; }
QPushButton#compare:hover { background: #a9d9de; }
QPushButton#compare:pressed { background: #7cb8bf; }
QPushButton#compare:disabled { background: #2a3438; color: #a0adb0; }
QPushButton:focus, QPushButton#compare:focus { border-color: @focus; }
QPushButton#compare:focus { border-color: #ffffff; }
QListView { background: transparent; border: none; outline: 0; }
QScrollBar:vertical { background: #202326; border: none; width: 10px; }
QScrollBar::handle:vertical { background: #697478; min-height: 28px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #899397; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QProgressBar { background: #303a3e; border: none; border-radius: 2px; }
QProgressBar::chunk { background: @accent; border-radius: 2px; }
QToolTip { color: @ink; background: @surface; border: 1px solid #566166; padding: 6px; }
"""
for token, value in {
    "@ground": GROUND,
    "@surface": SURFACE,
    "@rule": RULE,
    "@ink": INK,
    "@secondary": SECONDARY,
    "@accent": ACCENT,
    "@focus": FOCUS,
}.items():
    STYLESHEET = STYLESHEET.replace(token, value)


def label(text: str = "", style: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(style)
    return widget


def divider() -> QFrame:
    line = QFrame(objectName="divider")
    line.setFixedHeight(1)
    return line


def tooltip(text: str) -> str:
    return f'<span style="white-space: pre-wrap">{escape(text)}</span>'


def draw_symbol(painter: QPainter, kind: str, bounds: QRectF, color: str) -> None:
    """Draw the same outline weight for folder and status symbols."""
    painter.save()
    painter.translate(bounds.topLeft())
    painter.scale(bounds.width() / 20, bounds.height() / 20)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.35, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    if kind == "folder":
        path.moveTo(3, 4.5)
        path.lineTo(7.5, 4.5)
        path.lineTo(9.5, 6.5)
        path.lineTo(17, 6.5)
        path.quadTo(18, 6.5, 18, 7.5)
        path.lineTo(18, 15.5)
        path.quadTo(18, 17, 16.5, 17)
        path.lineTo(3.5, 17)
        path.quadTo(2, 17, 2, 15.5)
        path.lineTo(2, 6)
        path.quadTo(2, 4.5, 3, 4.5)
        path.closeSubpath()
    else:
        painter.drawEllipse(QRectF(2, 2, 16, 16))
        if kind == "check":
            path.moveTo(6, 10.2)
            path.lineTo(8.7, 12.8)
            path.lineTo(14, 7.5)
        elif kind == "clock":
            path.moveTo(10, 5.8)
            path.lineTo(10, 10)
            path.lineTo(13, 11.8)
        else:
            path.moveTo(10, 6)
            path.lineTo(10, 10.7)
            path.moveTo(10, 13.8)
            path.lineTo(10, 14)
    painter.drawPath(path)
    painter.restore()


def set_symbol(widget: QLabel, kind: str, size: int, color: str = SECONDARY) -> None:
    scale = widget.devicePixelRatioF()
    pixmap = QPixmap(round(size * scale), round(size * scale))
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        draw_symbol(painter, kind, QRectF(0, 0, size, size), color)
    widget.setPixmap(pixmap)
    widget.setFixedSize(size, size)


def add_copy_action(widget: QWidget, title: str, get_path) -> QAction:
    action = QAction(title, widget)
    action.setShortcut(QKeySequence.StandardKey.Copy)
    action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)

    def copy_path():
        path = get_path()
        if path:
            QApplication.clipboard().setText(str(path))

    action.triggered.connect(copy_path)
    widget.addAction(action)
    widget.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
    return action


class FolderName(QLabel):
    """Keep a folder's full name available while eliding the visible line."""

    def __init__(self):
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.name = ""

    def show_folder(self, path: Path | None) -> None:
        self.name = (path.name or str(path)) if path is not None else "No folder selected"
        weight, color = (500, INK) if path is not None else (400, SECONDARY)
        self.setStyleSheet(f"font-size: 16px; font-weight: {weight}; color: {color};")
        self.setAccessibleName(self.name)
        self.setToolTip(tooltip(str(path)) if path is not None else "")
        self.refresh_text()

    def refresh_text(self) -> None:
        self.setText(self.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, self.width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_text()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height())


@dataclass(frozen=True)
class MissingFolder:
    path: str
    classification: Classification | None
    error: str = ""

    @property
    def format_label(self) -> str:
        return self.classification.audio_format.value if self.classification else "Format unavailable"

    @property
    def unknown_label(self) -> str:
        count = self.classification.unknown_files if self.classification else 0
        if not count:
            return ""
        noun = "file" if count == 1 else "files"
        return f"{count:,} unknown {noun}"


class FolderPaths(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.folders: list[MissingFolder] = []

    def set_folders(self, folders: list[MissingFolder]) -> None:
        self.beginResetModel()
        self.folders = folders
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.folders)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.folders):
            return None
        folder = self.folders[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return folder.path
        if role == Qt.ItemDataRole.UserRole:
            return folder
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{folder.path}, {folder.format_label}"
        if role == Qt.ItemDataRole.AccessibleDescriptionRole:
            return folder.error or folder.unknown_label
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = (folder.path, folder.format_label, folder.error or folder.unknown_label)
            return "<br>".join(tooltip(line) for line in lines if line)
        return None


class ResultRow(QStyledItemDelegate):
    """Paint name and location while the model keeps the full relative path."""

    def __init__(self, parent: QListView):
        super().__init__(parent)
        self.name_font = QFont(parent.font())
        self.name_font.setPixelSize(15)
        self.name_font.setWeight(QFont.Weight.Medium)
        self.location_font = QFont(parent.font())
        self.location_font.setPixelSize(13)

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, 60)

    def paint(self, painter, option, index) -> None:
        folder = index.data(Qt.ItemDataRole.UserRole)
        parent, _, name = folder.path.rpartition("/")
        location = parent.replace("/", " / ") if parent else "Top-level folder"
        row = option.rect
        state = option.state

        painter.save()
        painter.setClipRect(row)
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(row.adjusted(0, 0, 0, -1), QColor("#2a383d"))
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(row.adjusted(0, 0, 0, -1), QColor("#202326"))
        painter.fillRect(QRect(row.x(), row.bottom(), row.width(), 1), QColor(RULE))
        if state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(FOCUS), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(row).adjusted(1, 1, -1, -2), 4, 4)

        draw_symbol(painter, "folder", QRectF(row.x() + 15, row.y() + 20, 20, 20), SECONDARY)
        classification_width = 134
        content_left = row.x() + 48
        format_left = row.right() - 15 - classification_width
        content_width = max(0, format_left - content_left - 12)
        name_line = QRect(content_left, row.y() + 10, content_width, 22)
        location_line = QRect(content_left, row.y() + 31, content_width, 19)
        format_line = QRect(format_left, name_line.y(), classification_width, name_line.height())
        self.draw_line(painter, name, self.name_font, INK, name_line)
        self.draw_line(painter, location, self.location_font, SECONDARY, location_line,
                       elide=Qt.TextElideMode.ElideLeft)
        self.draw_line(painter, folder.format_label, self.location_font, INK, format_line,
                       alignment=Qt.AlignmentFlag.AlignRight)
        if folder.unknown_label:
            unknown_line = QRect(format_left, location_line.y(), classification_width, location_line.height())
            self.draw_line(painter, folder.unknown_label, self.location_font, SECONDARY, unknown_line,
                           alignment=Qt.AlignmentFlag.AlignRight)
        painter.restore()

    @staticmethod
    def draw_line(painter, text, font, color, bounds, *,
                  alignment=Qt.AlignmentFlag.AlignLeft, elide=Qt.TextElideMode.ElideRight) -> None:
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(bounds, Qt.AlignmentFlag.AlignVCenter | alignment,
                         painter.fontMetrics().elidedText(text, elide, bounds.width()))


class ComparisonThread(QThread):
    classifying = Signal()
    succeeded = Signal(object, object)
    failed = Signal(str)

    def __init__(self, original: Path, current: Path, parent: QWidget):
        super().__init__(parent)
        self.original = original
        self.current = current

    def run(self) -> None:
        try:
            result = compare_libraries(self.original, self.current)
            folders = []
            if result.missing:
                self.classifying.emit()
                for path in result.missing:
                    try:
                        classification = classify_folder(self.original, path)
                    except ClassificationError as error:
                        folders.append(MissingFolder(path, None, str(error)))
                    else:
                        folders.append(MissingFolder(path, classification))
        except ComparisonError as error:
            self.failed.emit(str(error))
        except Exception:
            logging.error("Unexpected error during library comparison.")
            self.failed.emit("Something interrupted the comparison. Try again.")
        else:
            self.succeeded.emit(result, folders)


class LibraryPicker(QWidget):
    changed = Signal()

    def __init__(self, title: str):
        super().__init__()
        self.path: Path | None = None
        self.title = title
        self.dialog_title = "Choose " + title.lower()

        heading = label(title, "libraryLabel")
        self.folder_name = FolderName()
        self.folder_name.show_folder(None)
        self.choose_button = QPushButton("Choose folder…")
        self.choose_button.setAccessibleName(self.dialog_title + " folder")
        self.choose_button.setMinimumSize(138, 44)
        self.choose_button.clicked.connect(self.choose)
        self.copy_action = add_copy_action(self.choose_button, "Copy folder path", lambda: self.path)
        self.copy_action.setEnabled(False)

        icon = QLabel()
        set_symbol(icon, "folder", 20)
        names = QVBoxLayout()
        names.setSpacing(3)
        names.addWidget(heading)
        names.addWidget(self.folder_name)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(icon)
        layout.addLayout(names, 1)
        layout.addWidget(self.choose_button)

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
        self.choose_button.setFocus()
        if folder:
            self.path = Path(folder)
            self.folder_name.show_folder(self.path)
            self.choose_button.setText("Change…")
            self.choose_button.setAccessibleName("Change " + self.title.lower() + " folder")
            self.choose_button.setAccessibleDescription(str(self.path))
            self.choose_button.setToolTip(tooltip(str(self.path)))
            self.copy_action.setEnabled(True)
            self.changed.emit()


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("window")
        self.setWindowTitle(TITLE)
        self.setWindowIcon(QIcon(str(EMBLEM)))
        self.setMinimumSize(470, 520)
        self.resize(700, 602)
        self.thread: ComparisonThread | None = None
        self.close_after_scan = False
        self.restore_compare_focus = False

        frame = QHBoxLayout(self)
        frame.setContentsMargins(36, 34, 36, 20)
        canvas = QWidget()
        canvas.setMaximumWidth(780)
        frame.addWidget(canvas, 1)
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(label("Compare libraries", "title"))
        layout.addSpacing(6)
        layout.addWidget(label("Find what's missing from your collection.", "subtitle"))
        layout.addSpacing(26)

        libraries = QWidget(objectName="libraries")
        library_layout = QVBoxLayout(libraries)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(0)
        self.original = LibraryPicker("Original library")
        self.current = LibraryPicker("Current library")
        library_layout.addWidget(self.original)
        library_layout.addWidget(divider())
        library_layout.addWidget(self.current)
        layout.addWidget(libraries)
        layout.addSpacing(14)

        action = QHBoxLayout()
        action.setSpacing(9)
        self.status_icon = QLabel()
        self.status = label(style="status")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Comparison progress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(42, 4)
        self.progress.hide()
        self.compare_button = QPushButton("Compare libraries", objectName="compare")
        self.compare_button.setMinimumHeight(44)
        action.addWidget(self.status_icon)
        action.addWidget(self.status, 1)
        action.addWidget(self.progress)
        action.addWidget(self.compare_button)
        layout.addLayout(action)
        layout.addSpacing(26)

        result_heading = QHBoxLayout()
        result_heading.addWidget(label("Missing content", "sectionTitle"))
        result_heading.addStretch()
        self.count = label(style="count")
        result_heading.addWidget(self.count)
        layout.addLayout(result_heading)
        layout.addSpacing(8)
        layout.addWidget(divider())

        self.result_pages = QStackedWidget()
        state_page = QWidget()
        state_layout = QVBoxLayout(state_page)
        state_layout.setContentsMargins(16, 16, 16, 0)
        state_layout.setSpacing(4)
        self.state_title = label(style="stateTitle")
        self.state_detail = label(style="detail")
        self.state_detail.setWordWrap(True)
        self.state_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        state_layout.addWidget(self.state_title)
        state_layout.addWidget(self.state_detail)
        state_layout.addStretch()
        self.result_pages.addWidget(state_page)

        self.paths = QListView()
        self.paths.setAccessibleName("Missing folders in the original library")
        self.paths.setMouseTracking(True)
        self.paths.setUniformItemSizes(True)
        self.paths.setResizeMode(QListView.ResizeMode.Adjust)
        self.paths.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.paths.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.paths.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.path_model = FolderPaths(self)
        self.paths.setModel(self.path_model)
        self.paths.setItemDelegate(ResultRow(self.paths))
        copy_result = add_copy_action(self.paths, "Copy relative path", lambda: self.paths.currentIndex().data())
        copy_result.setEnabled(False)
        self.paths.selectionModel().currentChanged.connect(
            lambda current, previous: copy_result.setEnabled(current.isValid())
        )
        self.result_pages.addWidget(self.paths)
        layout.addWidget(self.result_pages, 1)

        self.original.changed.connect(self.reset_results)
        self.current.changed.connect(self.reset_results)
        self.compare_button.clicked.connect(self.start_comparison)
        self.setTabOrder(self.original.choose_button, self.current.choose_button)
        self.setTabOrder(self.current.choose_button, self.compare_button)
        self.setTabOrder(self.compare_button, self.paths)
        self.reset_results()

    def set_status(self, text: str, kind: str, color: str = SECONDARY) -> None:
        self.status.setText(text)
        self.status_icon.setVisible(bool(kind))
        if kind:
            set_symbol(self.status_icon, kind, 17, color)

    def show_message(self, title: str = "", detail: str = "") -> None:
        self.path_model.set_folders([])
        self.count.hide()
        self.state_title.setText(title)
        self.state_title.setVisible(bool(title))
        self.state_detail.setText(detail)
        self.state_detail.setVisible(bool(detail))
        self.result_pages.setCurrentIndex(0)

    @Slot()
    def reset_results(self) -> None:
        ready = self.original.path is not None and self.current.path is not None
        self.compare_button.setEnabled(ready)
        self.set_status("Ready to compare." if ready else "Choose both library folders.", "")
        self.show_message(detail="Missing folders will appear here after a comparison.")

    @Slot()
    def start_comparison(self) -> None:
        original, current = self.original.path, self.current.path
        if original is None or current is None or self.thread is not None:
            return

        self.restore_compare_focus = self.compare_button.hasFocus()
        self.show_message()
        self.set_status("Comparing libraries…", "clock")
        self.progress.show()
        self.original.choose_button.setEnabled(False)
        self.current.choose_button.setEnabled(False)
        self.compare_button.setEnabled(False)

        # Only the worker reads the filesystem. Signals return results to the UI thread.
        self.thread = ComparisonThread(original, current, self)
        self.thread.classifying.connect(self.show_classification_progress)
        self.thread.succeeded.connect(self.show_result)
        self.thread.failed.connect(self.show_error)
        self.thread.finished.connect(self.finish_comparison)
        self.thread.start()

    @Slot()
    def show_classification_progress(self) -> None:
        if not self.close_after_scan:
            self.set_status("Checking audio formats…", "clock")

    @Slot(object, object)
    def show_result(self, result: ComparisonResult, folders: list[MissingFolder]) -> None:
        count = len(folders)
        if count:
            self.path_model.set_folders(folders)
            self.result_pages.setCurrentWidget(self.paths)
            self.paths.scrollToTop()
        else:
            self.show_message("No missing folders found.")
        noun = "folder" if count == 1 else "folders"
        self.count.setText(f"{count:,} {noun}")
        self.count.show()

        warning = any(folder.error for folder in folders)
        status = "Some audio formats couldn’t be checked." if warning else "Comparison complete."
        if result.skipped_links:
            links = "link" if result.skipped_links == 1 else "links"
            status += f" {result.skipped_links:,} {links} skipped."
        self.set_status(status, "warning" if warning else "check", WARNING if warning else ACCENT)
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(
            self.status, f"{count:,} missing {noun}. {status}"
        ))

    @Slot(str)
    def show_error(self, text: str) -> None:
        self.show_message(detail=text)
        self.set_status("Comparison couldn’t finish.", "warning", WARNING)
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(
            self.status, f"Comparison couldn’t finish. {text}"
        ))

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
        elif self.restore_compare_focus:
            self.compare_button.setFocus()

    def closeEvent(self, event) -> None:
        if self.thread is not None:
            self.close_after_scan = True
            self.set_status("Closing after comparison…", "clock")
            event.ignore()
            return
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    app.setWindowIcon(QIcon(str(EMBLEM)))
    app.setStyle("Fusion")

    palette = app.palette()
    for role, color in (
        (QPalette.ColorRole.Window, GROUND),
        (QPalette.ColorRole.Base, GROUND),
        (QPalette.ColorRole.WindowText, INK),
        (QPalette.ColorRole.Text, INK),
        (QPalette.ColorRole.Button, SURFACE),
        (QPalette.ColorRole.ButtonText, INK),
        (QPalette.ColorRole.Highlight, "#36535a"),
        (QPalette.ColorRole.HighlightedText, INK),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)

    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if "Inter" in QFontDatabase.families():
        font.setFamily("Inter")
    app.setFont(font)

    window = Window()
    window.setStyleSheet(STYLESHEET)
    window.show()
    exit_code = app.exec()
    if window.thread is not None:
        window.thread.wait()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
