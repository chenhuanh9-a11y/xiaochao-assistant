# Verified installed download state

Base: remote main 8469e664 (PR27 not merged at fetch). Independent branch; frozen candidate and runtime directories untouched.

Online index verification runs on its existing background worker. Receipt ownership/schema and every installed file's size/SHA must match before a card says installed; a different archive digest means update, missing/changed files mean repair. Download completion alone never marks installation. Live import revalidates its committed receipt/content in its worker, then the existing current-request/generation/mode/root guarded owner-thread consumer publishes card state after refreshing resource/catalog selections. An import invalidates only its target, cancels prior online generation, and retains unrelated verified cards. Failed/cancelled targets remain unverified until a new index check, rather than making an unproved rollback claim.

No new dependency or standalone module. Existing star_map_import module gains verification helper. No route plaza or UI redesign. Tests use temporary receipts/files, actual import ZIP/lock/rollback cases and production controller methods, no real GUI/game. A new index check also verifies helper-installed packages after restart. External file changes after verification become visible on next index check; there is no file watcher. Cancellation is checked between files, not during one hash read. Long-file verification is background work, not a guaranteed immediate cancellation.

Tests: test_installed_package_status, test_live_author_import_controller, test_live_author_import. No raw private data in commit.
