from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import traceback
from typing import Any, Callable


class HangWatchdog:
    """Capture bounded Python thread stacks when the UI heartbeat stalls.

    The watchdog never takes the assistant's UI, capture, alignment, or log
    locks. It samples only after a heartbeat deadline is missed and writes at
    most ``max_snapshots`` compact JSONL records, so the evidence path cannot
    become an unbounded producer during a hang.
    """

    def __init__(
        self,
        *,
        output_dir: Path,
        process_id: int,
        build_fingerprint: str,
        state_getter: Callable[[], dict[str, Any]] | None = None,
        interval_s: float = 0.25,
        stall_s: float = 0.75,
        cooldown_s: float = 2.0,
        max_snapshots: int = 3,
        max_stack_chars: int = 6000,
    ) -> None:
        if interval_s <= 0 or stall_s <= 0 or cooldown_s <= 0:
            raise ValueError("watchdog timing values must be positive")
        if max_snapshots <= 0 or max_stack_chars <= 0:
            raise ValueError("watchdog bounds must be positive")
        self.output_dir = Path(output_dir)
        self.process_id = int(process_id)
        self.build_fingerprint = str(build_fingerprint)
        self.state_getter = state_getter
        self.interval_s = float(interval_s)
        self.stall_s = float(stall_s)
        self.cooldown_s = float(cooldown_s)
        self.max_snapshots = int(max_snapshots)
        self.max_stack_chars = int(max_stack_chars)
        self._heartbeat_at = time.monotonic()
        self._last_snapshot_at = 0.0
        self._snapshot_count = 0
        self._start_lock = threading.Lock()
        self._heartbeat_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def snapshot_count(self) -> int:
        with self._heartbeat_lock:
            return int(self._snapshot_count)

    def heartbeat(self) -> None:
        with self._heartbeat_lock:
            self._heartbeat_at = time.monotonic()

    def start(self, stop_event: threading.Event) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                args=(stop_event,),
                name="identityv-hang-watchdog",
                daemon=True,
            )
            self._thread.start()

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.interval_s):
            now = time.monotonic()
            with self._heartbeat_lock:
                age_s = now - self._heartbeat_at
                snapshot_count = self._snapshot_count
                last_snapshot_at = self._last_snapshot_at
            if age_s < self.stall_s or snapshot_count >= self.max_snapshots:
                continue
            if now - last_snapshot_at < self.cooldown_s:
                continue
            self._write_snapshot(age_s)

    def _write_snapshot(self, heartbeat_age_s: float) -> None:
        with self._heartbeat_lock:
            if self._snapshot_count >= self.max_snapshots:
                return
            self._snapshot_count += 1
            snapshot_number = self._snapshot_count
            self._last_snapshot_at = time.monotonic()
        frames = sys._current_frames()
        thread_meta = {
            thread.ident: {
                "name": thread.name,
                "daemon": bool(thread.daemon),
                "alive": bool(thread.is_alive()),
            }
            for thread in threading.enumerate()
        }
        stacks = []
        for thread_id, frame in sorted(frames.items(), key=lambda item: item[0]):
            stack = "".join(traceback.format_stack(frame, limit=80))
            stacks.append({
                "threadId": int(thread_id),
                **thread_meta.get(thread_id, {}),
                "stack": stack[-self.max_stack_chars:],
            })
        try:
            state = dict(self.state_getter() if self.state_getter else {})
        except Exception as error:
            state = {"stateGetterError": f"{type(error).__name__}: {error}"}
        row = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "hang-watchdog-snapshot",
            "processId": self.process_id,
            "buildFingerprint": self.build_fingerprint,
            "snapshotNumber": snapshot_number,
            "heartbeatAgeMs": round(max(0.0, heartbeat_age_s) * 1000.0, 1),
            "state": state,
            "threads": stacks,
        }
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"thread-stacks-{self.process_id}.jsonl"
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
        except OSError:
            # The watchdog must not feed a second failure back into the UI.
            return
