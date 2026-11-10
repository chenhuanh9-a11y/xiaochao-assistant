"""Production resource ownership begins before readers and ends after workers."""
from contextlib import contextmanager

from managed_base_resources import repair_managed_base_resources
from resource_transaction import assert_resources_ready, resource_read_lock, resource_runtime_lease, resource_write_lock


@contextmanager
def program_runtime(root, *, production, startup_report=None):
    """Keep Setup away from running code; imported data has its own lock/root.

    Ordinary startup never repairs or seeds old installation map resources.
    Installer/reset interruptions keep their existing program-side protection.
    Other legacy resource journals are retained for explicit recovery.
    """
    if not production:
        yield
        return
    from pathlib import Path
    from resource_transaction import assert_installer_upgrade_ready, ResourceTransactionError, _safe
    with resource_runtime_lease(root):
        assert_installer_upgrade_ready(root)
        reset = _safe(Path(root) / 'work/map-packages/pending-resource-reset.json')
        if reset.exists():
            raise ResourceTransactionError('resource-reset-recovery-required')
        if startup_report is not None:
            legacy = [name for name in ('pending-base-repair.json', 'pending-upgrade.json',
                'pending-additions.json', 'pending-removal.json', 'pending-author-snapshot.json')
                if (Path(root) / 'work/map-packages' / name).exists()]
            startup_report.update(status='imported-resources', legacyPending=legacy)
        yield


@contextmanager
def production_resources(root, *, production, on_failure=None, startup_report=None):
    if not production:
        yield
        return
    with resource_runtime_lease(root):
        # Recovery/repair must complete before any provider or cache is created.
        # The lifetime lease allows our short write lock while excluding helpers
        # and installers until all resource consumers have drained at shutdown.
        try:
            result = repair_managed_base_resources(root)
            if startup_report is not None:
                startup_report.update(status=result.get('status'), reasons=sorted({
                    row['reason'] for row in result.get('retirementDeferred', [])}))
            from map_package_upgrade import retire_completed_upgrades
            with resource_write_lock(root):
                retire_completed_upgrades(root)
            assert_resources_ready(root)
        except Exception as error:
            outcome = on_failure(error) if on_failure is not None else None
            if outcome is None:
                raise
            # Recovery consent/launch occurs while the lease still excludes
            # other readers. The caller must exit without constructing a client.
            yield outcome
            return
        yield None


@contextmanager
def readable_resources(root, *, production):
    """Read-only self-check: reject pending writes; never repair or recover."""
    if not production:
        yield
        return
    with resource_read_lock(root):
        assert_resources_ready(root)
        yield
