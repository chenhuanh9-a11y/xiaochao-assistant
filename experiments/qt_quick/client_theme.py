"""Application-local light palette for the client's Qt Quick Basic controls.

Keep these colors aligned with ClientPalette.qml and DESIGN.md. Basic's TextField/ComboBox/Popup
have a style palette independent of the application palette; ClientPalette.qml
provides their inherited QML roles.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


def light_palette() -> QPalette:
    """Resolve all Basic color roles, including inactive and disabled windows."""
    palette = QPalette()
    # Basic delegates use Light + HighlightedText; input selection uses
    # Highlight + HighlightedText. Keep both combinations readable.
    colors = {
        "Window": "#f3f6fc", "WindowText": "#142b4b",
        "Base": "#ffffff", "AlternateBase": "#edf2fa",
        "Text": "#142b4b", "PlaceholderText": "#526785",
        "Button": "#f1f5fc", "ButtonText": "#354963",
        "BrightText": "#ffffff", "Highlight": "#668eed",
        "HighlightedText": "#142b4b", "Accent": "#2f66eb",
        "Light": "#e7effc", "Midlight": "#e7effc", "Mid": "#8da4c4",
        "Dark": "#526785", "Shadow": "#354f75",
        "Link": "#245de7", "LinkVisited": "#183a73",
        "ToolTipBase": "#ffffff", "ToolTipText": "#142b4b",
    }
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, value in colors.items():
            palette.setColor(group, getattr(QPalette.ColorRole, role), QColor(value))
    for role in ("WindowText", "Text", "ButtonText", "PlaceholderText"):
        palette.setColor(QPalette.Disabled, getattr(QPalette.ColorRole, role), QColor("#62738c"))
    for role in ("Base", "Button"):
        palette.setColor(QPalette.Disabled, getattr(QPalette.ColorRole, role), QColor("#edf1f6"))
    palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor("#d6e0ef"))
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor("#526785"))
    palette.setColor(QPalette.Disabled, QPalette.Accent, QColor("#8da4c4"))
    return palette


def apply_light_theme(app: QGuiApplication) -> None:
    """Call before creating QML engines; never changes the OS color preference.

    Qt 6.8+ supports this per-application override. Set the explicit palette
    after the hint: a platform theme can refresh its palette on scheme change.
    Qt retains explicitly assigned palette roles when system colors change.
    """
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setPalette(light_palette())
