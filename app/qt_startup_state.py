"""Bounded, code-only startup milestones; no Qt, resources, files or threads."""
from collections import deque
import threading
import time

LABELS = {
    "runtime-entry": "正在加载运行组件…",
    "validate-profile": "正在核对程序身份…",
    "import-client": "正在加载界面组件…",
    "instance-check": "正在检查已有实例…",
    "resource-check": "正在检查本地资源与上次操作…",
    "create-services": "正在准备界面服务…",
    "create-windows": "正在创建主界面…",
    "main-constructed": "正在绘制主界面…",
    "main-frame-submitted": "主界面已提交绘制",
    "context-ready": "当前模式资源已准备好",
    "context-unavailable": "当前模式资源不可用",
}


class StartupState:
    def __init__(self, clock=time.monotonic):
        self._clock, self._start = clock, clock()
        self._lock = threading.Lock()
        self._rows, self._seen = deque(maxlen=16), set()
        self._stage = "runtime-entry"
        self.origin = "python-module-import"
        self.mark(self._stage)

    def runtime_hook(self):
        with self._lock:
            if self._seen == {"runtime-entry"}:
                self.origin = "python-runtime-hook"

    def mark(self, stage):
        if stage not in LABELS:
            raise ValueError("Unknown startup milestone")
        with self._lock:
            if stage in self._seen:
                return
            self._seen.add(stage)
            self._stage = stage
            self._rows.append(dict(stage=stage, elapsedMs=max(0., (self._clock()-self._start)*1000)))

    def snapshot(self):
        with self._lock:
            return [dict(row) for row in self._rows]

    def label(self):
        with self._lock:
            return LABELS[self._stage]


STATE = StartupState()
