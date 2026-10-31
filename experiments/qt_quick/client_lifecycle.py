"""Small flushed startup/shutdown journal; no capture, configuration or polling.

Normal problem events use the asynchronous ProblemsService. These few boundary
records must survive an event loop that is stopping or a late Python finalizer.
"""
from datetime import datetime, timezone
import json
from logging.handlers import RotatingFileHandler
import logging
import os
from pathlib import Path
import threading


class LifecycleJournal:
    def __init__(self, work_root):
        self.path = Path(work_root) / 'nightmare-live' / f'events-qt-lifecycle-{os.getpid()}.jsonl'

    def record_event(self, event):
        handler = None
        try:
            from client_paths import _no_links
            _no_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            allowed = ('stage', 'sourceCommit', 'buildFingerprint', 'closing', 'closeReady',
                       'hookRunning', 'pools', 'pendingWrites', 'exitCode', 'reason')
            row = {key: event[key] for key in allowed if key in event}
            row.update(time=datetime.now(timezone.utc).isoformat(), event='qt-lifecycle', processId=os.getpid(),
                       pythonThreads=[dict(name=t.name[:80], ident=t.ident, daemon=t.daemon)
                                      for t in threading.enumerate()[:16]])
            message = json.dumps(row, ensure_ascii=False, default=str)
            if len(message) > 8192:
                return
            handler = RotatingFileHandler(self.path, maxBytes=1024 * 1024, backupCount=1, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(message)s'))
            handler.emit(logging.LogRecord('qt-lifecycle', logging.INFO, '', 0, message, (), None))
            handler.flush()
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics cannot prevent normal shutdown.
        finally:
            if handler is not None:
                try:
                    handler.close()
                except (OSError, ValueError):
                    pass
