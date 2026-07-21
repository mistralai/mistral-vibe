from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QGuiApplication,
    QMouseEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from vibe.core.pawgress.events import ControlAction, IslandState
from vibe.core.pawgress.protocol import ControlMsg
from vibe.overlay.cat import CatAnimator
from vibe.overlay.macos import make_visible_on_all_spaces
from vibe.overlay.registry import SessionEntry
from vibe.overlay.render import (
    MUTED,
    _span as _span_html,
    buttons_html,
    detail_nav_html,
    directory_row_html,
    render_island_html,
)

_ACTIONS: dict[str, ControlAction] = {
    "pause": ControlAction.PAUSE,
    "resume": ControlAction.RESUME,
    "stop": ControlAction.STOP,
    "focus_vibe": ControlAction.FOCUS_VIBE,
    "allow_once": ControlAction.ALLOW_ONCE,
    "allow_session": ControlAction.ALLOW_SESSION,
    "allow_always": ControlAction.ALLOW_ALWAYS,
    "deny": ControlAction.DENY,
}

_STYLE = """
#island {
    background: rgb(17, 20, 28);
    border-radius: 18px;
    border: 1px solid rgb(255, 130, 5);
}
#divider {
    background: rgb(42, 46, 58);
    border: none;
}
QLabel {
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 13px;
    color: #e6e6e6;
}
"""

_MARGIN_SIDE = 16
_MARGIN_TOP = 12
_MARGIN_BOTTOM = 9
_SPACING = 7
_BORDER = 2
_DRAG_THRESHOLD_PX = 8
TICK_SECONDS = 0.16


class IslandWindow(QWidget):
    control_requested = Signal(object)
    row_selected = Signal(str)  # a directory row was clicked → open its detail
    open_directory_requested = Signal()  # ‹ all — back to the directory

    def __init__(self) -> None:  # noqa: PLR0915
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setStyleSheet(_STYLE)

        self._cat = CatAnimator()
        self._state: IslandState | None = None
        self._drag_offset: QPoint | None = None
        self.active_control_sid: str | None = None
        self._mode = "detail"  # "detail" | "directory"
        self._entries: list[SessionEntry] = []
        self._active_sid: str | None = None
        self._active_index = 0
        self._ticks = 0
        self._state_ticks = 0
        self._user_moved = False
        self._user_resized = False
        self._programmatic_resize = False
        self._press_pos = QPoint()

        frame = QFrame(self)
        frame.setObjectName("island")

        self._label = QLabel(frame)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setOpenExternalLinks(False)
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._label.linkActivated.connect(self._on_link)
        self._label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        divider = QFrame(frame)
        divider.setObjectName("divider")
        divider.setFixedHeight(1)

        self._buttons = QLabel(frame)
        self._buttons.setTextFormat(Qt.TextFormat.RichText)
        self._buttons.setOpenExternalLinks(False)
        self._buttons.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._buttons.linkActivated.connect(self._on_link)
        self._buttons.setAlignment(Qt.AlignmentFlag.AlignCenter)

        inner = QVBoxLayout(frame)
        inner.setContentsMargins(
            _MARGIN_SIDE, _MARGIN_TOP, _MARGIN_SIDE, _MARGIN_BOTTOM
        )
        inner.setSpacing(_SPACING)
        inner.addWidget(self._label, 1)
        inner.addWidget(divider)
        inner.addWidget(self._buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        self._grip = QSizeGrip(self)
        self._grip.resize(16, 16)
        self.setMinimumSize(240, 140)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(160)
        self._render()
        self._resize()

    def set_sessions(
        self, entries: list[SessionEntry], active_sid: str | None, active_index: int
    ) -> None:
        """Socket path: full session list + which one is active (its detail slide)."""
        self._entries = entries
        self._active_sid = active_sid
        self.active_control_sid = active_sid
        self._active_index = active_index
        self._state = next((e.state for e in entries if e.sid == active_sid), None)
        self._state_ticks = self._ticks
        self._render()
        self._resize()
        if entries:
            make_visible_on_all_spaces(self)

    def update_state(self, state: IslandState | None) -> None:
        """Demo/single path (stdin): one session, always the detail view."""
        self._mode = "detail"
        self._entries = []
        self._state = state
        self._state_ticks = self._ticks
        self._render()
        self._resize()
        if state is not None:
            make_visible_on_all_spaces(self)

    @property
    def mode(self) -> str:
        return self._mode

    def enter_directory(self) -> None:
        self._mode = "directory"
        self._render()
        self._resize()

    def enter_detail(self) -> None:
        self._mode = "detail"
        self._render()
        self._resize()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def _tick(self) -> None:
        self._ticks += 1
        self._cat.next_frame()
        self._render()
        if self._ticks % 3 == 0:
            self._follow_cursor_screen()
            make_visible_on_all_spaces(self)

    def _follow_cursor_screen(self) -> None:
        target = QGuiApplication.screenAt(QCursor.pos())
        if target is None or self.screen() is target:
            return
        geo = target.availableGeometry()
        self._user_moved = False
        self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 8)

    def _render(self) -> None:
        if self._mode == "directory" and self._entries:
            self._render_directory()
        else:
            self._render_detail()

    def _render_directory(self) -> None:
        rows = [
            _span_html(f"\U0001f43e Pawgress · {len(self._entries)} sessions", MUTED)
        ]
        for entry in self._entries:
            status = entry.state.state if entry.state else None
            rows.append(
                directory_row_html(
                    entry.sid,
                    entry.label,
                    status,
                    entry.model,
                    entry.terminal,
                    entry.elapsed,
                    entry.sid == self._active_sid,
                )
            )
        self._label.setText("<br>".join(rows))
        self._buttons.setText("")

    def _render_detail(self) -> None:
        if self._state is None:
            self._label.setText(
                '<span style="color:#8a8a8a">\U0001f43e Pawgress · idle</span>'
            )
            self._buttons.setText("")
            return
        total = len(self._entries)
        header = ""
        if total > 1:
            header = detail_nav_html(self._active_index, total) + "<br>"
        island = render_island_html(
            self._state,
            self._cat.current_frame(),
            self._ticks,
            with_buttons=False,
            age_seconds=int((self._ticks - self._state_ticks) * TICK_SECONDS),
        )
        self._label.setText(header + island)
        self._buttons.setText(buttons_html(self._state))

    def _resize(self) -> None:
        if not self._user_resized:
            content = self._label.sizeHint()
            buttons = self._buttons.sizeHint()
            width = max(content.width(), buttons.width()) + 2 * _MARGIN_SIDE + _BORDER
            height = (
                content.height()
                + buttons.height()
                + 1
                + 2 * _SPACING
                + _MARGIN_TOP
                + _MARGIN_BOTTOM
                + _BORDER
            )
            self._programmatic_resize = True
            self.resize(
                max(width, self.minimumWidth()), max(height, self.minimumHeight())
            )
            self._programmatic_resize = False
        self._reposition()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._follow_cursor_screen()
        make_visible_on_all_spaces(self)
        QTimer.singleShot(100, lambda: make_visible_on_all_spaces(self))

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.WinIdChange and self.isVisible():
            QTimer.singleShot(0, lambda: make_visible_on_all_spaces(self))
        return handled

    def resizeEvent(self, event: QResizeEvent) -> None:
        dragging = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        if not self._programmatic_resize and dragging:
            self._user_resized = True
        self._grip.move(self.width() - 20, self.height() - 20)
        super().resizeEvent(event)

    def _reposition(self) -> None:
        screen = (
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        if screen is None or self._user_moved:
            return
        geo = screen.availableGeometry()
        self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 8)

    def _on_link(self, href: str) -> None:
        if href == "quit":
            instance = QApplication.instance()
            if instance is not None:
                instance.quit()
            return
        if href.startswith("row:"):
            self.row_selected.emit(href[len("row:") :])
            return
        if href == "open_directory":
            self.open_directory_requested.emit()
            return
        action = _ACTIONS.get(href)
        if action is None:
            return
        self._emit_control(action)

    def _emit_control(self, action: ControlAction) -> None:
        request_id = self._state.request_id if self._state else None
        self.control_requested.emit(ControlMsg(action=action, request_id=request_id))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint()
            self._drag_offset = point - self.frameGeometry().topLeft()
            self._press_pos = point

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        point = event.globalPosition().toPoint()
        if (
            not self._user_moved
            and (point - self._press_pos).manhattanLength() < _DRAG_THRESHOLD_PX
        ):
            return
        self._user_moved = True
        self.move(point - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
