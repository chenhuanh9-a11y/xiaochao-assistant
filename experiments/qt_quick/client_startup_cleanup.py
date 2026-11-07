"""Drain every owned resource consumer, including partially built Qt clients."""
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QThreadPool, QTimer


class ClientCleanupError(RuntimeError):
    code = "resource-consumers-not-stopped"

    def __init__(self, failures):
        self.failures = tuple(failures)
        super().__init__("Client cleanup failed: " + ", ".join(name for name, _error in failures))


def shutdown_client(client):
    if getattr(client, "_shutdown_complete", False):
        error = getattr(client, "_shutdown_error", None)
        if error is not None:
            raise error
        return
    if getattr(client, "_shutdown_running", False):
        return
    client._shutdown_running = True
    client._closed = True
    failures = []

    def attempt(name, action):
        try:
            action()
        except Exception as error:
            failures.append((name, error))

    def call(name, method="shutdown"):
        owner = getattr(client, name, None)
        action = getattr(owner, method, None)
        if callable(action):
            attempt(name + "." + method, action)

    def event(stage):
        lifecycle = getattr(client, "lifecycle", None)
        if lifecycle is not None:
            attempt("lifecycle." + stage, lambda: lifecycle.record_event(dict(stage=stage)))

    owners = [value for name, value in vars(client).items()
              if name != "app" and isinstance(value, QObject)]
    evidence = getattr(client, "g_cycle_evidence", None)
    if evidence is not None:
        owners.append(evidence)
    pools = {}
    for owner in owners:
        if not isinstance(owner, QObject):
            continue
        for pool in owner.findChildren(QThreadPool):
            pools[id(pool)] = pool
        for key in ("pool", "_pool"):
            pool = getattr(owner, key, None)
            if isinstance(pool, QThreadPool):
                pools[id(pool)] = pool

    def drain():
        # Timed waits let queued completions restore capture windows and start
        # the next already accepted log batch even after app.exec has returned.
        # No timeout may release a lease while a consumer is still running.
        stable = 0
        while stable < 2:
            done = True
            for pool in pools.values():
                done = pool.waitForDone(10) and done
            QCoreApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            pending = any(bool(getattr(owner, "pendingWrites", False)) for owner in owners)
            active = (any(pool.activeThreadCount() for pool in pools.values())
                      or bool(getattr(getattr(client, "runtime", None), "private_evidence_busy", False)))
            source = getattr(client, "input", None)
            hook_running = bool(getattr(source, "snapshot", {}).get("hookRunning", False))
            stable = stable + 1 if done and not active and not pending and not hook_running else 0

    try:
        event("shutdown-entered")
        timer = getattr(client, "_initialize_timer", None)
        if timer is not None:
            attempt("initialize-timer.stop", timer.stop)
        # Stop producers before waiting. Discover descendant timers too, so a
        # failed shutdown member cannot leave other owner timers scheduling work.
        for owner in owners:
            if not isinstance(owner, QObject):
                continue
            for timer in owner.findChildren(QTimer):
                attempt(type(owner).__name__ + ".timer.stop", timer.stop)
        coordinator = getattr(client, "coordinator", None)
        ready = bool(getattr(coordinator, "closeReady", False))
        if not ready:
            call("_import_handoff", "cancel")
            request = getattr(client, "_recovery_request", None)
            if request:
                from star_map_recovery_helper import cancel_recovery_request
                attempt("recovery.cancel", lambda: cancel_recovery_request(client.sandbox, request))
        # These are non-blocking cancellation/stop boundaries. Explicit input
        # and capture shutdown still run if coordinator.shutdown itself fails.
        call("route_plaza", "cancel")
        call("runtime", "stop_private_evidence")
        call("g_cycle_evidence")
        for name in ("coordinator", "input", "capture", "capture_exclusion", "session", "resources",
                     "package_manager", "chest_auto_tap", "lifecycle_trace"):
            call(name)
        call("online_packages", "cancel")
        call("reference_preview", "close")
        drain()
        if evidence is not None:
            # Zero pending writes precedes the worker's final return. Its last
            # outcome may also enqueue a main-thread diagnostic callback.
            attempt("g_cycle_evidence.join", lambda: evidence.shutdown(wait=True))
            drain()
        call("problems")
        for name in ("settings", "preview", "control_snap", "backend", "route_choices", "info", "tutorial",
                     "custom_galleries", "online_packages", "route_plaza", "manual_map_picker", "windows"):
            call(name)
        # Some shutdown methods finish accepted writes; settle their final
        # queued callbacks and verify all pools again, not just session.busy.
        drain()
        trace = getattr(client, "lifecycle_trace", None)
        thread = getattr(trace, "_thread", None)
        if thread is not None:
            thread.join()
        client._shutdown_complete = True
        if not failures:
            event("resource-workers-drained")
            event("shutdown-completed")
        if failures:
            error = client._shutdown_error = ClientCleanupError(failures)
            raise error from failures[0][1]
    finally:
        client._shutdown_running = False
