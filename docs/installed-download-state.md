# Verified installed download state

Base: remote main 8469e664 (PR27 not merged at fetch). Independent branch; frozen candidate and runtime directories untouched.

Online index verification runs on its existing background worker. Receipt ownership/schema and every installed file's size/SHA must match before a card says installed; a different archive digest means update, missing/changed files mean repair. Download completion alone never marks installation. Live import revalidates its committed receipt/content in its worker, then the existing current-request/generation/mode/root guarded owner-thread consumer publishes card state after refreshing resource/catalog selections. An import invalidates only its target, cancels prior online generation, and retains unrelated verified cards. Failed/cancelled targets remain unverified until a new index check, rather than making an unproved rollback claim.

No new dependency or standalone module. Existing star_map_import module gains verification helper. No route plaza or UI redesign. Tests use temporary receipts/files, actual import ZIP/lock/rollback cases and production controller methods, no real GUI/game. A new index check also verifies helper-installed packages after restart. External file changes after verification become visible on next index check; there is no file watcher. Cancellation is checked between files, not during one hash read. Long-file verification is background work, not a guaranteed immediate cancellation.

Tests: test_installed_package_status, test_live_author_import_controller, test_live_author_import. No raw private data in commit.

## Installed package reuse (2026-11-05)

Qt verifies the current receipt, archive SHA and every installed file in the existing worker before downloading. A complete installation of the selected archive skips transport and import even after startup has pruned its ZIP. Changed or missing files, an invalid receipt and a different archive SHA still take the normal download/import path. The card offers local verification for an installation confirmed by the latest index check. Cancellation, selection changes and local resource generations guard completion on the owner thread.

Validation: `PYTHONPATH=experiments/qt_quick:app QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software /tmp/xiaochao-qt-venv/bin/python -m unittest experiments/qt_quick/test_client_downloads.py experiments/qt_quick/test_client_resources.py tests/test_installed_package_status.py tests/test_live_author_import.py tests/test_map_package_upgrade.py`: 87 tests, 2 existing platform skips. Synthetic installed files use the actual receipt verifier; transport is controlled. The offscreen QML button dispatch also verifies that an installed package triggers neither a download nor an import. No Windows native download-lock, real game or released Setup validation is claimed.

The change reuses the project's existing verifier and worker lifecycle; no transport dependency was added. Reviewed references: [Qt QThreadPool](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html), [Python urllib.request](https://docs.python.org/3/library/urllib.request.html), and [pip's bounded download implementation](https://github.com/pypa/pip/blob/main/src/pip/_internal/network/download.py) (MIT, reference only; no copied code).
