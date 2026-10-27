from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
import threading
import tkinter as tk
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = SOURCE_ROOT / "work" / "nightmare-front-live-test"
TEST_SETTINGS = TEST_ROOT / "ui-settings.json"
os.environ.setdefault("IDENTITYV_NIGHTMARE_LIVE_ROOT", str(TEST_ROOT / "logs"))
sys.path[:0] = [str(SOURCE_ROOT / "work" / "vendor"), str(SOURCE_ROOT / "app")]

from PIL import ImageTk

from front_door_live_test import (
    CleanFrontDoorPreviews,
    FrontDoorLiveTestEngine,
    front_candidate_card_text,
)
from identityv_daily_assistant import (
    DailyAssistant,
    probable_map_interface,
    report_unhandled_error,
)
from identityv_live_mvp import detect_map_interface
from nightmare_candidate import NightmareCandidateEngine, NightmareMemoryStore


TEST_VERSION = "front-live-test-0.1"


def build_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        SOURCE_ROOT / "app" / "front_door_live_test.py",
        SOURCE_ROOT / "app" / "front_door_recognition.py",
        SOURCE_ROOT / "app" / "front_door_layout_recognition.py",
        SOURCE_ROOT / "app" / "front_door_groups.py",
        SOURCE_ROOT / "app" / "b1_relocalization.py",
        SOURCE_ROOT / "app" / "identityv_daily_assistant.py",
        SOURCE_ROOT / "app" / "nightmare_candidate.py",
        SOURCE_ROOT / "references" / "nightmare-front-door-clean-previews.json",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:8].upper()


BUILD_FINGERPRINT = build_fingerprint()


def prepare_test_settings(path: Path = TEST_SETTINGS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {}
    payload.update({
        "onboardingCompleted": True,
        "hotkey": str(payload.get("hotkey") or "G"),
        "routeProfileId": str(payload.get("routeProfileId") or "wang-qingxin"),
        "frontLiveTest": True,
        "frontLiveTestVersion": TEST_VERSION,
    })
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


class FrontLiveTestAssistant(DailyAssistant):
    """Isolated front-entry identity experiment over the verified daily flow."""

    LOCKED_ROUND_CAPTURE_REQUIRES_FLOOR_EVIDENCE = True

    def __init__(self, root: tk.Tk, start_background: bool = True) -> None:
        self.front_clean_previews: CleanFrontDoorPreviews | None = None
        self._candidate_photo_mode: str | None = None
        settings_path = prepare_test_settings()
        super().__init__(
            root,
            start_background=False,
            debug_feedback=True,
            settings_path=settings_path,
        )
        test_memory_root = TEST_ROOT / "memory"
        self.memory_store = NightmareMemoryStore(
            self.pack,
            root=test_memory_root,
            sample_validator=lambda frame: probable_map_interface(
                detect_map_interface(frame)
            ),
        )
        base_engine = NightmareCandidateEngine(
            self.pack, memory_store=self.memory_store,
        )
        self.engine = FrontDoorLiveTestEngine(
            base_engine,
            log_callback=self._log,
            diagnostic_root=TEST_ROOT / "diagnostics" / "front-pose-failures",
        )
        self.front_clean_previews = CleanFrontDoorPreviews()
        self.candidate_photos.clear()
        self._candidate_photo_mode = None
        self.root.title("第五人格小抄 · 正门识别贴合测试版")
        if hasattr(self, "build_label"):
            self.build_label.configure(
                text=f"{TEST_VERSION} · {BUILD_FINGERPRINT} · 不进入日用版",
            )
        self._refresh_candidates()
        self._set_status(
            "正门实机测试：一楼自动识别，证据不足时显示正门缩略图",
            "#f6c453",
        )
        self._log(
            "front-live-test-started",
            testVersion=TEST_VERSION,
            buildFingerprint=BUILD_FINGERPRINT,
            isolatedMemoryRoot=str(test_memory_root),
            dailyAssistantSourceHookAdded=True,
            dailyAssistantDefaultBehaviorChanged=False,
            routeDisplayRequiresCurrentFrameAlignment=True,
        )
        if start_background:
            self.root.after(self.EVENT_POLL_MS, self._poll_events)
            threading.Thread(target=self._preload, daemon=True).start()

    def _refresh_candidates(self, preserve_confirmation: bool = False) -> None:
        previews = getattr(self, "front_clean_previews", None)
        mode = "front" if getattr(self, "current_floor", "2") == "1" else "side"
        if previews is not None and mode != getattr(self, "_candidate_photo_mode", None):
            self.candidate_photos.clear()
            if mode == "front":
                for item in self.pack.maps:
                    try:
                        self.candidate_photos[item.id] = ImageTk.PhotoImage(
                            previews.build(item.id),
                        )
                    except Exception:
                        pass
            self._candidate_photo_mode = mode
        super()._refresh_candidates(preserve_confirmation=preserve_confirmation)
        if previews is None or mode != "front":
            return
        confirmed = self.gate.confirmed_id if preserve_confirmation else None
        for item in self.visible_maps:
            if not self.candidates.exists(item.id):
                continue
            self.candidates.item(
                item.id,
                text=front_candidate_card_text(
                    item,
                    self.auto_scores.get(item.id),
                    confirmed=item.id == confirmed,
                ),
            )
        if confirmed not in self.pack.by_id:
            top = max(
                self.auto_scores.values(),
                key=lambda row: float(row.get("score", 0.0)),
                default=None,
            )
            detected = str((top or {}).get("frontDoorLayout") or "unknown")
            if detected == "unknown":
                self.candidate_hint.configure(
                    text="正门识别不足 · 请按正门缩略图人工选择",
                    fg="#f6c453",
                )
            else:
                label = str((top or {}).get("frontDoorLayoutLabel") or detected)
                self.candidate_hint.configure(
                    text=f"正门门型：{label} · 候选按当前帧分数排序",
                    fg="#5ee0a3",
                )


def validate() -> dict:
    previews = CleanFrontDoorPreviews()
    root = tk.Tk()
    root.withdraw()
    try:
        assistant = FrontLiveTestAssistant(root, start_background=False)
        result = {
            "success": (
                len(previews.rows) == 13
                and len(assistant.pack.by_id) == 13
                and isinstance(assistant.engine, FrontDoorLiveTestEngine)
            ),
            "schema": "identityv-nightmare-front-live-test-validation-v1",
            "testVersion": TEST_VERSION,
            "buildFingerprint": BUILD_FINGERPRINT,
            "mapCount": len(assistant.pack.by_id),
            "frontPreviewCount": len(previews.rows),
            "dailyAssistantSourceHookAdded": True,
            "dailyAssistantDefaultBehaviorChanged": False,
            "automaticMapSubmissionScope": "development-front-test-only",
            "routeDisplayRequiresCurrentFrameAlignment": True,
            "memoryRoot": str(assistant.memory_store.root),
        }
        assistant.close()
        return result
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="噩梦正门识别＋贴合独立实机测试版")
    parser.add_argument("--validate", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.validate:
        result = validate()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["success"]:
            raise SystemExit(2)
        return
    mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Local\\IdentityVMapDailyAssistant",
    )
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(
            None,
            "日用助手或正门测试版已经在运行。请先关闭旧助手，再启动本测试版。",
            "正门识别贴合测试版",
            0x40,
        )
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)
        return
    try:
        root = tk.Tk()
        root.report_callback_exception = report_unhandled_error
        FrontLiveTestAssistant(root)
        root.mainloop()
    finally:
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        report_unhandled_error(*sys.exc_info())
        raise SystemExit(1)
