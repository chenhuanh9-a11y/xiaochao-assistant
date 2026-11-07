"""Run before PyInstaller's Qt hook loads QtCore; no Qt/product resource IO."""
import atexit
import sys

from qt_startup_splash import close_startup_splash, show_startup_splash

atexit.register(close_startup_splash)
show_startup_splash(sys.argv[1:])
