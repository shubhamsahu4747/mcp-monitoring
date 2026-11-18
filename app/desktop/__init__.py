"""Desktop application layer for Walled AI Security Gateway."""

from app.desktop.application import WalledAIDesktopApp, run_desktop
from app.desktop.authorization_popup import AuthorizationPopup
from app.desktop.main_window import MainWindow
from app.desktop.notifications import NotificationService
from app.desktop.request_presenter import RequestPresenter
from app.desktop.theme import MAIN_WINDOW_STYLESHEET, POPUP_STYLESHEET
from app.desktop.tray import SystemTrayManager

__all__ = [
    "AuthorizationPopup",
    "MainWindow",
    "NotificationService",
    "RequestPresenter",
    "SystemTrayManager",
    "WalledAIDesktopApp",
    "MAIN_WINDOW_STYLESHEET",
    "POPUP_STYLESHEET",
    "run_desktop",
]
