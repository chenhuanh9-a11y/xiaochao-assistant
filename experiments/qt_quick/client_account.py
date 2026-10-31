"""Qt account facade. HTTP stays on workers; identity is invalidated on ingress.

Shutdown is nonblocking: the owner calls shutdown(), then includes pool in its
existing drain barrier (or polls is_drained()) before destroying the facade.
urllib requests use the account service's finite socket timeout; cancellation
rejects results, it does not claim to abort an in-flight socket.
"""
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot

from client_paths import runtime_paths
from account_service import AccountError, AccountService, call


class _Signals(QObject):
    done = Signal(int, str, object, str)


class _Work(QRunnable):
    def __init__(self, generation, operation, function):
        super().__init__()
        self.generation, self.operation, self.function = generation, operation, function
        self.signals = _Signals()

    def run(self):
        function, self.function = self.function, None
        result, message = None, ""
        try:
            if function is not None:
                result = function()
        except AccountError as error:
            message = str(error)
        except Exception:
            # Never send traceback, request body or arbitrary exception text to UI/logs.
            message = "账号操作未完成，请稍后重试"
        finally:
            function = None  # Release the login closure and its password promptly.
        self.signals.done.emit(self.generation, self.operation, result, message)


class ClientAccountService(QObject):
    changed = Signal()

    def __init__(self, sandbox, *, service=None, vault=None, api=call, parent=None):
        super().__init__(parent)
        paths = runtime_paths(sandbox)
        self.service = service or AccountService(paths.settings_path.parent / "account", vault=vault, api=api)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._closed = False
        self._generation = 0
        self._jobs = {}
        self._operation = ""
        self._state = self.service.snapshot()
        self._message = "可恢复上次会话" if self._state["canRestore"] else "登录已有星途账号"
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll_state)
        self._timer.start()

    loggedIn = Property(bool, lambda self: self._state["loggedIn"], notify=changed)
    username = Property(str, lambda self: self._state["username"], notify=changed)
    busy = Property(bool, lambda self: bool(self._jobs), notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    persistent = Property(bool, lambda self: self._state["persistent"], notify=changed)
    sessionSaved = Property(bool, lambda self: self._state["sessionSaved"], notify=changed)
    storageWarning = Property(str, lambda self: self._state["storageWarning"], notify=changed)
    canRestore = Property(bool, lambda self: self._state["canRestore"], notify=changed)
    canCancel = Property(bool, lambda self: bool(self._jobs) and self._operation in ("login", "restore"), notify=changed)
    revision = Property(int, lambda self: self._state["revision"], notify=changed)

    @Slot()
    def _poll_state(self):
        if self._closed:
            return
        state = self.service.snapshot()
        if state != self._state:
            if self._state["loggedIn"] and not state["loggedIn"] and not self._jobs:
                self._message = "登录已失效，请重新登录"
            self._state = state
            self.changed.emit()

    def _start(self, operation, function, message):
        self._generation += 1
        self._operation = operation
        job = _Work(self._generation, operation, function)
        self._jobs[self._generation] = job
        job.signals.done.connect(self._complete, Qt.QueuedConnection)
        self._message = message
        self._state = self.service.snapshot()
        self.changed.emit()
        self.pool.start(job)

    @Slot(str, str)
    def login(self, identifier, password):
        if self._closed or self._jobs or self.loggedIn:
            return
        try:
            work = self.service.prepare_login(identifier, password)
        except AccountError as error:
            self._message = str(error)
            self.changed.emit()
            return
        self._start("login", work, "正在登录…")

    @Slot()
    def restoreSession(self):
        if self._closed or self._jobs or self.loggedIn:
            return
        if not self.canRestore:
            self._message = "没有可恢复的会话，请登录"
            self.changed.emit()
            return
        self._start("restore", self.service.prepare_restore(), "正在恢复会话…")

    @Slot()
    def logout(self):
        if self._closed or self._operation == "logout" and self._jobs:
            return
        # Invalidate before queueing: even an already-running refresh cannot
        # restore the old identity while revocation waits behind a login worker.
        work = self.service.prepare_logout()
        self._start("logout", work, "已退出本地会话，正在结束服务器会话…")

    @Slot()
    def cancel(self):
        if self._closed or not self.canCancel:
            return
        self.service.cancel_pending()
        self._generation += 1
        self._operation = "cancelled"
        self._message = "已取消，正在等待当前请求结束…"
        self._state = self.service.snapshot()
        self.changed.emit()

    @Slot(int, str, object, str)
    def _complete(self, generation, operation, result, error):
        self._jobs.pop(generation, None)
        if self._closed:
            return
        self._state = self.service.snapshot()
        if generation == self._generation:
            if error:
                self._message = error
            elif operation == "logout":
                self._message = "已退出登录" if result else "已退出本地会话；服务器退出请求未完成"
            elif self._state["loggedIn"]:
                self._message = "已登录" if operation == "login" else "会话已恢复"
                if not self._state["sessionSaved"]:
                    self._message += "；本次会话未保存，关闭后需重新登录"
            else:
                self._message = "没有可恢复的会话，请登录"
        elif not self._jobs and self._operation == "cancelled":
            self._message = "操作已取消"
        self.changed.emit()

    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._timer.stop()
        self.service.close()
        # Running jobs keep only their local callable. Queued callables (which
        # may contain a password) are released even if QThreadPool clears them.
        for job in self._jobs.values():
            job.function = None
        self.pool.clear()
        self._state = self.service.snapshot()

    def is_drained(self):
        drained = self.pool.waitForDone(0)
        if drained:
            self._jobs.clear()
        return drained
