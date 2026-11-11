"""Parent-bound full uninstall; never used by the upgrade path."""
from installer_uninstall import main


if __name__ == '__main__':
    raise SystemExit(main())
