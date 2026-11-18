"""Enterprise security visual theme and QSS stylesheets for Walled AI Desktop."""

from __future__ import annotations

# Color Palette: Modern Enterprise Security Aesthetic
COLOR_BG_PRIMARY = "#FFFFFF"
COLOR_BG_SECONDARY = "#F8FAFC"  # Slate 50
COLOR_BG_TERTIARY = "#F1F5F9"   # Slate 100
COLOR_BG_DARK = "#0F172A"       # Slate 900
COLOR_BG_DARK_CARD = "#1E293B"  # Slate 800
COLOR_BG_CODE = "#1E293B"       # Slate 800

COLOR_BORDER = "#E2E8F0"        # Slate 200
COLOR_BORDER_STRONG = "#CBD5E1" # Slate 300
COLOR_BORDER_DARK = "#334155"   # Slate 700

COLOR_TEXT_PRIMARY = "#0F172A"  # Slate 900
COLOR_TEXT_SECONDARY = "#475569"# Slate 600
COLOR_TEXT_MUTED = "#94A3B8"    # Slate 400
COLOR_TEXT_LIGHT = "#F8FAFC"    # Slate 50

COLOR_BRAND_PRIMARY = "#0284C7" # Sky 600
COLOR_BRAND_HOVER = "#0369A1"   # Sky 700
COLOR_BRAND_LIGHT = "#E0F2FE"   # Sky 100

COLOR_SUCCESS = "#16A34A"       # Green 600
COLOR_SUCCESS_HOVER = "#15803D" # Green 700
COLOR_SUCCESS_BG = "#DCFCE7"    # Green 100
COLOR_SUCCESS_TEXT = "#15803D"

COLOR_DANGER = "#DC2626"        # Red 600
COLOR_DANGER_HOVER = "#B91C1C"  # Red 700
COLOR_DANGER_BG = "#FEE2E2"     # Red 100
COLOR_DANGER_TEXT = "#B91C1C"

COLOR_WARNING = "#D97706"       # Amber 600
COLOR_WARNING_BG = "#FEF3C7"    # Amber 100
COLOR_WARNING_TEXT = "#B45309"

COLOR_BADGE_SECURITY = "#0D9488"# Teal 600
COLOR_BADGE_SECURITY_BG = "#CCFBF1"

FONT_FAMILY_SANS = "Segoe UI, -apple-system, BlinkMacSystemFont, Arial, sans-serif"
FONT_FAMILY_MONO = "Consolas, 'Cascadia Code', 'Courier New', monospace"

POPUP_STYLESHEET = f"""
QDialog {{
    background-color: {COLOR_BG_SECONDARY};
    font-family: {FONT_FAMILY_SANS};
    color: {COLOR_TEXT_PRIMARY};
}}

QFrame#headerCard {{
    background-color: {COLOR_BG_DARK};
    border-radius: 8px;
    padding: 16px;
}}

QLabel#brandTitle {{
    color: {COLOR_TEXT_LIGHT};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

QLabel#securityBadge {{
    background-color: {COLOR_BADGE_SECURITY};
    color: {COLOR_TEXT_LIGHT};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#subtitle {{
    color: {COLOR_TEXT_MUTED};
    font-size: 12px;
}}

QFrame#detailsCard {{
    background-color: {COLOR_BG_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 14px;
}}

QLabel#metaLabel {{
    color: {COLOR_TEXT_SECONDARY};
    font-size: 12px;
    font-weight: 600;
}}

QLabel#metaValue {{
    color: {COLOR_TEXT_PRIMARY};
    font-size: 12px;
    font-weight: 500;
}}

QLabel#metaValueHighlight {{
    color: {COLOR_BRAND_PRIMARY};
    font-size: 13px;
    font-weight: 700;
    font-family: {FONT_FAMILY_MONO};
}}

QPlainTextEdit#jsonViewer {{
    background-color: {COLOR_BG_CODE};
    color: #E2E8F0;
    border: 1px solid #334155;
    border-radius: 6px;
    font-family: {FONT_FAMILY_MONO};
    font-size: 12px;
    line-height: 1.4;
    padding: 8px;
}}

QProgressBar#timeoutBar {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    text-align: center;
    background-color: {COLOR_BG_TERTIARY};
    height: 10px;
}}

QProgressBar#timeoutBar::chunk {{
    background-color: {COLOR_WARNING};
    border-radius: 3px;
}}

QPushButton#approveButton {{
    background-color: {COLOR_SUCCESS};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 13px;
    font-weight: 600;
}}

QPushButton#approveButton:hover {{
    background-color: {COLOR_SUCCESS_HOVER};
}}

QPushButton#approveButton:disabled {{
    background-color: #94A3B8;
}}

QPushButton#rejectButton {{
    background-color: {COLOR_DANGER};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 13px;
    font-weight: 600;
}}

QPushButton#rejectButton:hover {{
    background-color: {COLOR_DANGER_HOVER};
}}

QPushButton#rejectButton:disabled {{
    background-color: #94A3B8;
}}
"""

MAIN_WINDOW_STYLESHEET = f"""
QMainWindow {{
    background-color: {COLOR_BG_SECONDARY};
    font-family: {FONT_FAMILY_SANS};
}}

QWidget#centralWidget {{
    background-color: {COLOR_BG_SECONDARY};
}}

/* Header Banner */
QFrame#headerBanner {{
    background-color: {COLOR_BG_DARK};
    border-radius: 10px;
    padding: 14px 20px;
    border: 1px solid #1E293B;
}}

/* Stat Metric Cards */
QFrame#statCard {{
    background-color: {COLOR_BG_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 14px 16px;
}}

QFrame#statCard:hover {{
    border-color: {COLOR_BORDER_STRONG};
    background-color: #FAFAFA;
}}

QLabel#statTitle {{
    color: #64748B;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}

QLabel#statValue {{
    color: {COLOR_TEXT_PRIMARY};
    font-size: 24px;
    font-weight: 800;
    margin-top: 2px;
    margin-bottom: 2px;
}}

QLabel#statSub {{
    color: {COLOR_TEXT_MUTED};
    font-size: 11px;
    font-weight: 500;
}}

/* Tab View & Panes */
QTabWidget {{
    background-color: transparent;
}}

QTabWidget::pane {{
    background-color: {COLOR_BG_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 12px;
    top: -1px;
}}

QTabBar {{
    background-color: transparent;
}}

QTabBar::tab {{
    background-color: #F1F5F9;
    color: #64748B;
    font-weight: 600;
    font-size: 12px;
    padding: 10px 24px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    border: 1px solid #CBD5E1;
    border-bottom: none;
    margin-right: 4px;
}}

QTabBar::tab:selected {{
    background-color: {COLOR_BG_PRIMARY};
    color: {COLOR_BRAND_PRIMARY};
    font-weight: 700;
    border-top: 3px solid {COLOR_BRAND_PRIMARY};
    border-bottom: 1px solid {COLOR_BG_PRIMARY};
}}

QTabBar::tab:hover:!selected {{
    background-color: #E2E8F0;
    color: {COLOR_TEXT_PRIMARY};
}}

QWidget#pendingTab, QWidget#auditTab {{
    background-color: {COLOR_BG_PRIMARY};
    border-radius: 6px;
}}

QLabel#sectionHeaderLabel {{
    color: {COLOR_TEXT_PRIMARY};
    font-weight: 700;
    font-size: 15px;
    background-color: transparent;
}}

/* Empty State Card */
QFrame#emptyStateFrame {{
    background-color: {COLOR_BG_SECONDARY};
    border: 1px dashed {COLOR_BORDER_STRONG};
    border-radius: 8px;
    padding: 36px 20px;
}}

QLabel#emptyStateTitle {{
    color: {COLOR_TEXT_PRIMARY};
    font-size: 15px;
    font-weight: 700;
}}

QLabel#emptyStateSubtitle {{
    color: {COLOR_TEXT_SECONDARY};
    font-size: 12px;
    line-height: 1.5;
}}

/* Buttons */
QPushButton#primaryButton {{
    background-color: {COLOR_BRAND_PRIMARY};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 18px;
    font-weight: 600;
    font-size: 12px;
}}

QPushButton#primaryButton:hover {{
    background-color: {COLOR_BRAND_HOVER};
}}

QPushButton#secondaryButton {{
    background-color: {COLOR_BG_TERTIARY};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 12px;
}}

QPushButton#secondaryButton:hover {{
    background-color: #E2E8F0;
    border-color: #94A3B8;
}}

/* Search Input */
QLineEdit#searchInput {{
    background-color: white;
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
    color: {COLOR_TEXT_PRIMARY};
}}

QLineEdit#searchInput:focus {{
    border-color: {COLOR_BRAND_PRIMARY};
}}

/* Tables */
QTableCornerButton::section {{
    background-color: #F8FAFC;
    border: none;
}}

QHeaderView {{
    background-color: #F8FAFC;
    border: none;
}}

QHeaderView::section {{
    background-color: #F8FAFC;
    color: #475569;
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 10px 12px;
    border: none;
    border-bottom: 2px solid #E2E8F0;
}}

QTableWidget {{
    background-color: {COLOR_BG_PRIMARY};
    alternate-background-color: #F8FAFC;
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    font-size: 12px;
    color: {COLOR_TEXT_PRIMARY};
    outline: none;
}}

QTableWidget::item {{
    padding: 8px 12px;
    border-bottom: 1px solid #F1F5F9;
    color: {COLOR_TEXT_PRIMARY};
}}

QTableWidget::item:selected {{
    background-color: #E0F2FE;
    color: #0369A1;
}}

QStatusBar {{
    background-color: {COLOR_BG_PRIMARY};
    color: {COLOR_TEXT_SECONDARY};
    border-top: 1px solid {COLOR_BORDER};
    font-size: 11px;
    padding: 5px 10px;
}}
"""
