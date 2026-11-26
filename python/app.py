"""Music Library Recovery desktop entry point."""

import logging
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path
from threading import Event

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QAction,
    QColor,
    QDesktopServices,
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
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPlainTextEdit,
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

from library_recovery import (
    CATEGORIES, RecoveryCancelled, RecoveryError, RecoveryItem, RecoveryPlan, Status,
    prepare_recovery, recover,
)

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
QWidget#window, QDialog#recoveryDialog { background: @ground; color: @ink; }
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
QPushButton[primary="true"] { background: @accent; color: #132528; padding: 0 19px; }
QPushButton[primary="true"]:hover { background: #a9d9de; }
QPushButton[primary="true"]:pressed { background: #7cb8bf; }
QPushButton[primary="true"]:disabled { background: #2a3438; color: #a0adb0; }
QPushButton:focus { border-color: @focus; }
QPushButton[primary="true"]:focus { border-color: #ffffff; }
QPlainTextEdit { background: @surface; color: @ink; border: none; padding: 8px; }
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
    def eligible(self) -> bool:
        return bool(self.classification and self.classification.audio_format in CATEGORIES)

    @property
    def unknown_label(self) -> str:
        count = self.classification.unknown_files if self.classification else 0
        if not count:
            return ""
        noun = "file" if count == 1 else "files"
        return f"{count:,} unknown {noun}"


class FolderPaths(QAbstractListModel):
    checks_changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.folders: list[MissingFolder] = []
        self.eligible_rows: set[int] = set()
        self.checked_rows: set[int] = set()
        self.checks_enabled = True

    def set_folders(self, folders: list[MissingFolder]) -> None:
        self.beginResetModel()
        self.folders = folders
        self.eligible_rows = {i for i, folder in enumerate(folders) if folder.eligible}
        self.checked_rows.clear()
        self.endResetModel()
        self.checks_changed.emit()

    def flags(self, index):
        flags = super().flags(index)
        if self.checks_enabled and index.row() in self.eligible_rows:
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        row = index.row()
        if role != Qt.ItemDataRole.CheckStateRole or not self.checks_enabled or row not in self.eligible_rows:
            return False
        if value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value:
            self.checked_rows.add(row)
        else:
            self.checked_rows.discard(row)
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.AccessibleTextRole])
        self.checks_changed.emit()
        return True

    def toggle_all(self) -> None:
        if self.checked_rows == self.eligible_rows:
            self.checked_rows.clear()
        else:
            self.checked_rows = self.eligible_rows.copy()
        self.dataChanged.emit(self.index(0), self.index(len(self.folders) - 1),
                              [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.AccessibleTextRole])
        self.checks_changed.emit()

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
        if role == Qt.ItemDataRole.CheckStateRole and folder.eligible:
            return Qt.CheckState.Checked if index.row() in self.checked_rows else Qt.CheckState.Unchecked
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

        if folder.eligible:
            box = QRectF(row.x() + 16, row.y() + 22, 16, 16)
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(ACCENT if checked else "#778388"), 1.2))
            painter.setBrush(QColor(ACCENT) if checked else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 3, 3)
            if checked:
                painter.setPen(QPen(QColor("#132528"), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(QPointF(box.x() + 4, box.y() + 8), QPointF(box.x() + 7, box.y() + 11))
                painter.drawLine(QPointF(box.x() + 7, box.y() + 11), QPointF(box.x() + 12, box.y() + 5))
        else:
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

    def editorEvent(self, event, model, option, index) -> bool:
        if not index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return False
        area = QRect(option.rect.x() + 4, option.rect.y() + 8, 40, 44)
        pointer = (event.type() == QEvent.Type.MouseButtonRelease
                   and event.button() == Qt.MouseButton.LeftButton
                   and area.contains(event.position().toPoint()))
        key = event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
        if pointer or key:
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
            value = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
            return model.setData(index, value, Qt.ItemDataRole.CheckStateRole)
        return False

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


class RecoveryThread(QThread):
    advanced = Signal(int, int, str)

    def __init__(self, parent, *, selection=None, plan=None):
        super().__init__(parent)
        self.selection = selection
        self.plan = plan
        self.stop = Event()
        self.result = None
        self.error = ""
        self.preparation_cancelled = False

    def run(self) -> None:
        try:
            if self.selection is not None:
                self.result = prepare_recovery(*self.selection, cancelled=self.stop.is_set)
            else:
                self.result = recover(self.plan, cancelled=self.stop.is_set, progress=self.advanced.emit)
        except RecoveryCancelled:
            self.preparation_cancelled = True
        except RecoveryError as error:
            self.error = str(error)
        except Exception:
            logging.error("Unexpected error during recovery.")
            self.error = "Recovery was interrupted. Check the destination before trying again."


def readable_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} bytes"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024
        if value < 1024 or unit == "PiB":
            return f"{value:,.1f} {unit}"


class RecoveryDialog(QDialog):
    def __init__(self, parent, title, subtitle, destination: Path):
        super().__init__(parent)
        self.setObjectName("recoveryDialog")
        self.setWindowTitle(title)
        self.setStyleSheet(STYLESHEET)
        self.setMinimumWidth(430)
        self.resize(540, 340)
        self.destination = destination
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(24, 22, 24, 18)
        self.content.setSpacing(8)
        self.content.addWidget(label(title, "sectionTitle"))
        explanation = label(subtitle, "subtitle")
        explanation.setWordWrap(True)
        self.content.addWidget(explanation)
        self.content.addSpacing(6)
        self.content.addWidget(label("Recovery destination", "libraryLabel"))
        path = label(str(destination), "stateTitle")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.content.addWidget(path)

    def show_entries(self, entries) -> None:
        if not entries:
            return
        self.content.addSpacing(8)
        self.content.addWidget(divider())
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setAccessibleName("Folder details")
        details.setPlainText("\n\n".join(
            f"{entry.status.value} · {entry.path}" + (f"\n{entry.detail}" if entry.detail else "")
            for entry in entries
        ))
        self.content.addWidget(details, 1)

    def add_actions(self, dismiss, primary=None) -> None:
        self.content.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(dismiss)
        cancel.setMinimumHeight(42)
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        if primary is not None:
            primary.setMinimumHeight(42)
            primary.setDefault(False)
            primary.setAutoDefault(False)
            row.addWidget(primary)
        self.content.addLayout(row)
        cancel.setFocus()


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("window")
        self.setWindowTitle(TITLE)
        self.setWindowIcon(QIcon(str(EMBLEM)))
        self.setMinimumSize(470, 520)
        self.resize(700, 702)
        self.thread: ComparisonThread | None = None
        self.close_after_scan = False
        self.restore_compare_focus = False
        self.recovery_worker: RecoveryThread | None = None
        self.destination: Path | None = None

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
        self.compare_button.setProperty("primary", True)
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
        self.select_all = QPushButton("Select all")
        self.select_all.setMinimumHeight(36)
        self.select_all.hide()
        result_heading.addWidget(self.select_all)
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

        self.recovery_controls = QWidget()
        recovery_layout = QVBoxLayout(self.recovery_controls)
        recovery_layout.setContentsMargins(0, 8, 0, 0)
        recovery_layout.setSpacing(6)
        recovery_layout.addWidget(divider())
        destination_row = QHBoxLayout()
        destination_label = label("Recovery destination", "libraryLabel")
        self.destination_name = FolderName()
        self.destination_name.show_folder(None)
        destination_row.addWidget(destination_label)
        destination_row.addWidget(self.destination_name, 1)
        self.choose_destination = QPushButton("Choose…")
        self.choose_destination.setAccessibleName("Choose recovery destination")
        self.choose_destination.setMinimumHeight(40)
        self.choose_destination.clicked.connect(self.pick_destination)
        destination_row.addWidget(self.choose_destination)
        recovery_layout.addLayout(destination_row)
        self.selected_count = label("0 selected", "count")
        self.recover_button = QPushButton("Recover selected")
        self.recover_button.setProperty("primary", True)
        self.recover_button.setMinimumHeight(44)
        self.recover_button.clicked.connect(self.start_recovery)
        self.cancel_recovery = QPushButton("Cancel recovery")
        self.cancel_recovery.setMinimumHeight(44)
        self.cancel_recovery.clicked.connect(self.stop_recovery)
        self.cancel_recovery.hide()
        recovery_row = QHBoxLayout()
        recovery_row.addWidget(self.selected_count)
        recovery_row.addStretch()
        recovery_row.addWidget(self.cancel_recovery)
        recovery_row.addWidget(self.recover_button)
        recovery_layout.addLayout(recovery_row)
        layout.addWidget(self.recovery_controls)

        self.original.changed.connect(self.reset_results)
        self.current.changed.connect(self.reset_results)
        self.compare_button.clicked.connect(self.start_comparison)
        self.setTabOrder(self.original.choose_button, self.current.choose_button)
        self.setTabOrder(self.current.choose_button, self.compare_button)
        self.setTabOrder(self.compare_button, self.select_all)
        self.setTabOrder(self.select_all, self.paths)
        self.setTabOrder(self.paths, self.choose_destination)
        self.setTabOrder(self.choose_destination, self.recover_button)
        self.select_all.clicked.connect(self.path_model.toggle_all)
        self.path_model.checks_changed.connect(self.update_recovery_controls)
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
        if original is None or current is None or self.thread is not None or self.recovery_worker is not None:
            return

        self.restore_compare_focus = self.compare_button.hasFocus()
        self.show_message()
        self.set_status("Comparing libraries…", "clock")
        self.progress.setRange(0, 0)
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
        self.update_recovery_controls()
        if self.close_after_scan:
            self.close()
        elif self.restore_compare_focus:
            self.compare_button.setFocus()

    @Slot()
    def update_recovery_controls(self) -> None:
        model = self.path_model
        busy = self.recovery_worker is not None
        eligible = bool(model.eligible_rows)
        available = eligible and not busy and self.thread is None
        self.select_all.setVisible(eligible)
        self.select_all.setEnabled(available)
        self.select_all.setText("Deselect all" if model.checked_rows == model.eligible_rows else "Select all")
        self.recovery_controls.setVisible(eligible)
        self.setMinimumHeight(605 if eligible else 520)
        self.choose_destination.setEnabled(available)
        self.recover_button.setEnabled(available and bool(model.checked_rows) and self.destination is not None)
        self.recover_button.setVisible(not busy)
        self.cancel_recovery.setVisible(busy)
        self.selected_count.setText(f"{len(model.checked_rows):,} selected")
        model.checks_enabled = not busy
        primary = not eligible
        if self.compare_button.property("primary") != primary:
            self.compare_button.setProperty("primary", primary)
            self.compare_button.style().unpolish(self.compare_button)
            self.compare_button.style().polish(self.compare_button)

    @Slot()
    def pick_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose recovery destination", str(self.destination or ""),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.ReadOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if folder:
            self.destination = Path(folder)
            self.destination_name.show_folder(self.destination)
            self.choose_destination.setText("Change…")
            self.choose_destination.setAccessibleDescription(str(self.destination))
            self.choose_destination.setToolTip(tooltip(str(self.destination)))
            self.update_recovery_controls()
        self.choose_destination.setFocus()

    @Slot()
    def start_recovery(self) -> None:
        if self.thread is not None or self.recovery_worker is not None or not self.recover_button.isEnabled():
            return
        model = self.path_model
        selected = [
            RecoveryItem(model.folders[i].path, model.folders[i].classification)
            for i in sorted(model.checked_rows)
        ]
        self.begin_recovery_worker(
            selection=(self.original.path, self.current.path, self.destination, selected)
        )

    def begin_recovery_worker(self, *, selection=None, plan=None) -> None:
        self.recovery_worker = RecoveryThread(self, selection=selection, plan=plan)
        self.recovery_worker.finished.connect(self.finish_recovery_worker)
        self.recovery_worker.advanced.connect(self.recovery_progress)
        self.original.choose_button.setEnabled(False)
        self.current.choose_button.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.cancel_recovery.setEnabled(True)
        self.cancel_recovery.setText("Cancel recovery" if plan else "Cancel preparation")
        self.progress.setAccessibleName("Recovery progress")
        self.progress.setRange(0, len(plan.folders) if plan else 0)
        self.progress.setValue(0)
        self.progress.show()
        self.set_status("Preparing recovery…" if selection is not None else "Starting recovery…", "clock")
        self.update_recovery_controls()
        self.cancel_recovery.setFocus()
        self.recovery_worker.start()

    @Slot(int, int, str)
    def recovery_progress(self, completed: int, total: int, path: str) -> None:
        if self.recovery_worker is None or self.recovery_worker.stop.is_set():
            return
        self.progress.setValue(completed)
        if path:
            name = self.status.fontMetrics().elidedText(path.rsplit("/", 1)[-1], Qt.TextElideMode.ElideMiddle, 125)
            self.set_status(f"{completed:,} of {total:,} processed · {name}", "clock")
            self.status.setToolTip(tooltip(path))
        else:
            self.set_status(f"{completed:,} of {total:,} processed", "clock")

    @Slot()
    def stop_recovery(self) -> None:
        if self.recovery_worker is not None:
            self.recovery_worker.stop.set()
            self.cancel_recovery.setEnabled(False)
            self.set_status("Stopping recovery…", "clock")

    @Slot()
    def finish_recovery_worker(self) -> None:
        worker = self.recovery_worker
        self.recovery_worker = None
        self.progress.hide()
        self.progress.setAccessibleName("Comparison progress")
        self.status.setToolTip("")
        self.original.choose_button.setEnabled(True)
        self.current.choose_button.setEnabled(True)
        self.compare_button.setEnabled(True)
        self.update_recovery_controls()
        worker.deleteLater()
        if self.close_after_scan:
            self.close()
        elif worker.error:
            self.set_status("Recovery couldn’t continue.", "warning", WARNING)
            QMessageBox.warning(self, "Recovery couldn’t continue", worker.error)
        elif worker.preparation_cancelled or (worker.selection is not None and worker.stop.is_set()):
            self.set_status("Recovery preparation cancelled.", "")
        elif worker.selection is not None:
            self.confirm_recovery(worker.result)
        else:
            self.show_recovery_summary(worker.plan.destination.path, worker.result)
        if self.recovery_worker is None and not self.close_after_scan:
            self.recover_button.setFocus()

    def confirm_recovery(self, plan: RecoveryPlan) -> None:
        blocked = len(plan.skipped)
        count = len(plan.folders)
        summary = f"{plan.selected_count:,} selected · {count:,} ready · {readable_size(plan.size)} estimated"
        if blocked:
            summary += f" · {blocked:,} will be skipped"
        dialog = RecoveryDialog(self, "Review recovery", summary, plan.destination.path)
        if blocked:
            dialog.show_entries(plan.skipped)
        else:
            dialog.resize(540, 220)
        confirm = None
        if count:
            confirm = QPushButton("Start recovery")
            confirm.setProperty("primary", True)
            confirm.clicked.connect(dialog.accept)
        dialog.add_actions("Cancel" if count else "Close", confirm)
        if dialog.exec() == QDialog.DialogCode.Accepted and count:
            self.begin_recovery_worker(plan=plan)
        else:
            self.set_status("Recovery cancelled." if count else "No selected folders can be recovered.", "")

    def show_recovery_summary(self, destination: Path, results) -> None:
        tally = {status: sum(entry.status == status for entry in results) for status in Status}
        summary = " · ".join(f"{count:,} {status.value.lower()}" for status, count in tally.items())
        issues = any(tally[status] for status in (Status.SKIPPED, Status.FAILED, Status.CANCELLED))
        self.set_status("Recovery finished. Review the results." if issues else "Recovery complete.",
                        "warning" if issues else "check", WARNING if issues else ACCENT)
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self.status, f"Recovery results. {summary}"))
        dialog = RecoveryDialog(self, "Recovery results", summary, destination)
        dialog.show_entries(results)
        open_destination = QPushButton("Open destination")

        def open_folder():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination))):
                QMessageBox.warning(dialog, "Couldn’t open destination", "Open the recovery folder in your file manager.")

        open_destination.clicked.connect(open_folder)
        dialog.add_actions("Done", open_destination)
        dialog.exec()

    def closeEvent(self, event) -> None:
        if self.recovery_worker is not None:
            self.close_after_scan = True
            self.stop_recovery()
            event.ignore()
            return
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
    if window.recovery_worker is not None:
        window.recovery_worker.stop.set()
        window.recovery_worker.wait()
    if window.thread is not None:
        window.thread.wait()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
