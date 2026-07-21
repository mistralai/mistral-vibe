from __future__ import annotations

import sys
from typing import Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

_ACTIVATION_POLICY_ACCESSORY = 1


def _appkit() -> Any | None:
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        return None
    try:
        import AppKit
    except ImportError:
        return None
    return AppKit


def _ns_application() -> Any | None:
    appkit = _appkit()
    if appkit is None:
        return None
    return appkit.NSApplication.sharedApplication()


def _ns_window(widget: QWidget) -> Any | None:
    try:
        import objc
    except ImportError:
        return None
    objc_object = getattr(objc, "objc_object", None)
    if objc_object is None:
        return None
    try:
        handle = int(widget.winId())
        if handle == 0:
            return None
        ns_view = objc_object(c_void_p=handle)
        return ns_view.window()
    except Exception:
        return None


def hide_dock_icon() -> None:
    app = _ns_application()
    if app is None:
        return
    try:
        app.setActivationPolicy_(_ACTIVATION_POLICY_ACCESSORY)
    except Exception:
        return


def make_visible_on_all_spaces(widget: QWidget | None = None) -> None:
    if widget is None:
        return
    appkit = _appkit()
    if appkit is None:
        return
    ns_window = _ns_window(widget)
    if ns_window is None:
        return
    try:
        behavior = (
            appkit.NSWindowCollectionBehaviorMoveToActiveSpace
            | appkit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        ns_window.setCollectionBehavior_(behavior)

        if isinstance(ns_window, appkit.NSPanel):
            ns_window.setStyleMask_(
                ns_window.styleMask() | appkit.NSWindowStyleMaskNonactivatingPanel
            )
            ns_window.setFloatingPanel_(True)
            ns_window.setHidesOnDeactivate_(False)
            ns_window.setBecomesKeyOnlyIfNeeded_(True)

        ns_window.orderFrontRegardless()
        ns_window.setLevel_(appkit.NSScreenSaverWindowLevel)
    except Exception:
        return
