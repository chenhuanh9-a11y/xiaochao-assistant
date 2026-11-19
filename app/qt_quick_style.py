"""The packaged client only ships Qt Quick Controls Basic."""
import os


def pin_quick_controls_style():
    """Select Basic before any QML engine starts.

    File dialogs import ``QtQuick.Controls`` without a style suffix. Qt on
    Windows would otherwise select FluentWinUI3, which the installer does not
    contain. Explicit Basic imports stay Basic either way.
    """
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
