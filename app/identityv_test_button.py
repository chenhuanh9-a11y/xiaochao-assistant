from __future__ import annotations

import argparse
import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work" / "vendor"))
sys.path.insert(0, str(ROOT / "app"))

import cv2
import numpy as np

from identityv_live_mvp import (
    ASSETS,
    append_event,
    capture_client,
    detect_map_interface,
    detect_selected_floor,
    find_game_window,
    imread_unicode,
    imwrite_unicode,
    now_iso,
    serializable,
)
from runtime_map_recognition import RuntimeMapRecognizer
from map_overlay_prototype import build_aligned_overlay
from multipoint_map_overlay import build_multipoint_overlay


OUTPUT_ROOT = ROOT / "outputs" / "实机鉴别测试"
LATEST_STATUS = OUTPUT_ROOT / "latest_status.json"
VISION_INDEX = ASSETS / "maps" / "vision-index.json"
TAXONOMY = ROOT / "outputs" / "人类道路分类索引.json"
MARKED_POSTER = ROOT / "references" / "入口局部模板索引.webp"
# Probe almost immediately after the map key. If the opening animation is still
# in progress, retry briefly instead of imposing a fixed delay on every press.
# GDI capture is ~28 ms on the target machine (versus ~131 ms for PIL). Probe
# densely through the short opening animation so the first usable frame is not
# delayed by a slow full-screen grab.
MAP_OPEN_SETTLE_SECONDS = 0.01
MAP_CAPTURE_RETRIES = 16
MAP_CAPTURE_RETRY_SECONDS = 0.005
ACTIVE_UI_POLL_MS = 10


def commit_open_result(
    recognizer: RuntimeMapRecognizer,
    result: dict,
    selected_floor: int | None,
    map_still_open: bool,
) -> dict | None:
    """Commit identity evidence only while the triggering map view is open."""
    if not map_still_open:
        return None
    return recognizer.commit_result(result, selected_floor)


def hotkey_to_vk(value: str) -> tuple[str, int]:
    name = value.strip().upper()
    aliases = {
        "SPACE": 0x20,
        "空格": 0x20,
        "TAB": 0x09,
        "CAPSLOCK": 0x14,
        "CAPS": 0x14,
    }
    if name in aliases:
        return name, aliases[name]
    if len(name) == 1 and name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        return name, ord(name)
    if name.startswith("F") and name[1:].isdigit() and 1 <= int(name[1:]) <= 12:
        return name, 0x70 + int(name[1:]) - 1
    raise ValueError("键位仅支持 A-Z、0-9、F1-F12、Space、Tab 或 CapsLock")


def build_recognizer() -> RuntimeMapRecognizer:
    return RuntimeMapRecognizer(ASSETS, VISION_INDEX, TAXONOMY, MARKED_POSTER)


def save_map_sample(frame: np.ndarray, path: Path) -> bool:
    # Evidence must preserve the entire currently visible map. Fixed UI is kept
    # in the saved test artifact for audit, but recognition ignores it through
    # its anchor and map-content masks.
    return imwrite_unicode(path, frame)


def build_overlay_preview(
    frame: np.ndarray, overlay: np.ndarray, opacity: float
) -> np.ndarray:
    """Blend a full-opacity BGRA candidate onto its captured frame for review."""
    alpha = (
        overlay[:, :, 3:4].astype(np.float32)
        * float(np.clip(opacity, 0.0, 1.0))
        / 255.0
    )
    foreground = overlay[:, :, :3].astype(np.float32)
    background = frame[:, :, :3].astype(np.float32)
    return np.clip(foreground * alpha + background * (1.0 - alpha), 0, 255).astype(
        np.uint8
    )


def accumulate_alignment_evidence(
    history: dict[tuple[str, int], list[dict]],
    map_id: str,
    floor: int,
    metadata: dict,
) -> dict:
    """Require two geometrically compatible partial fits before saving review."""
    key = (map_id, int(floor))
    review_eligible = bool(metadata.get("reviewEligible"))
    scale = float(metadata.get("scale") or 0.0)
    rotation = float(metadata.get("rotationDeg") or 0.0)
    rows = history.setdefault(key, [])
    compatible = [
        row for row in rows[-12:]
        if review_eligible
        and row.get("reviewEligible")
        and row.get("scale", 0.0) > 0
        and abs(np.log(scale / row["scale"])) <= 0.08
        and abs(rotation - row["rotationDeg"]) <= 2.5
    ] if scale > 0 else []
    rows.append({
        "scale": scale,
        "rotationDeg": rotation,
        "inliers": int(metadata.get("inliers") or 0),
        "matchingMode": metadata.get("matchingMode"),
        "reviewEligible": review_eligible,
    })
    if len(rows) > 24:
        del rows[:-24]
    return {
        "requiredCompatibleFrames": 2,
        "compatibleFrames": len(compatible) + (1 if review_eligible else 0),
        "stableForReview": bool(review_eligible and len(compatible) >= 1),
        "routeFamiliesShareGeometry": True,
    }


def result_summary(result: dict) -> tuple[str, str, str]:
    marked = result.get("markedEvidence") or {}
    base_id = result.get("mapId") or ""
    marked_id = marked.get("mapId") or ""
    if result.get("success"):
        map_id = marked_id or base_id
        return "accepted", f"已确认 {map_id}", "#3ddc84"
    if base_id and marked_id and base_id != marked_id:
        return "conflict", f"冲突 {base_id} / {marked_id}", "#ffb84d"
    candidate = (result.get("sessionConsensus") or {}).get("candidateMapId") or marked_id or base_id
    if candidate:
        return "candidate", f"候选 {candidate} · 继续探索", "#ffd166"
    return "unknown", "未识别 · 继续探索", "#ff8a65"


def run_self_test(image_path: Path) -> dict:
    started = time.perf_counter()
    recognizer = build_recognizer()
    init_ms = (time.perf_counter() - started) * 1000
    image = imread_unicode(image_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取测试图片：{image_path}")
    floor_state = detect_selected_floor(image)
    started = time.perf_counter()
    result = recognizer.recognize(image, floor_state.get("selectedFloor"))
    recognition_ms = (time.perf_counter() - started) * 1000
    marked = result.get("markedEvidence") or {}
    return {
        "initMs": init_ms,
        "recognitionMs": recognition_ms,
        "success": bool(result.get("success")),
        "reason": result.get("reason"),
        "selectedFloor": floor_state.get("selectedFloor"),
        "evidenceMode": result.get("evidenceMode"),
        "baseCandidate": result.get("mapId"),
        "markedCandidate": marked.get("mapId"),
        "markedMargin": marked.get("margin"),
    }


class FloatingTestButton:
    def __init__(self, root: tk.Tk, exclude_capture: bool = True) -> None:
        self.root = root
        self.root.title("第五人格地图实机测试")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        width, height = 252, 174
        x = 24
        y = max(24, self.root.winfo_screenheight() - height - 82)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg="#111827")

        self.events: queue.Queue[dict] = queue.Queue()
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.recognizer: RuntimeMapRecognizer | None = None
        self.worker: threading.Thread | None = None
        self.running = False
        self.drag_origin = (0, 0)
        self.hotkey_name = "G"
        self.hotkey_vk = ord("G")
        self.overlay_window: tk.Toplevel | None = None
        self.overlay_label: tk.Label | None = None
        self.overlay_photo: tk.PhotoImage | None = None
        self.overlay_opacity = 0.48

        self.panel = tk.Frame(root, bg="#111827", highlightthickness=1, highlightbackground="#334155")
        self.panel.pack(fill="both", expand=True)
        self.title = tk.Label(
            self.panel, text="地图鉴别实机测试", bg="#111827", fg="#e5e7eb",
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
        )
        self.title.place(x=12, y=8, width=190, height=22)
        self.close_button = tk.Label(
            self.panel, text="×", bg="#111827", fg="#94a3b8",
            font=("Segoe UI", 13, "bold"), cursor="hand2",
        )
        self.close_button.place(x=220, y=5, width=24, height=24)
        self.close_button.bind("<Button-1>", lambda _: self.close())

        self.hotkey_label = tk.Label(
            self.panel, text="地图键", bg="#111827", fg="#94a3b8",
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.hotkey_label.place(x=12, y=34, width=44, height=22)
        self.hotkey_var = tk.StringVar(value="G")
        self.hotkey_entry = tk.Entry(
            self.panel, textvariable=self.hotkey_var, justify="center",
            bg="#1f2937", fg="#f8fafc", insertbackground="#f8fafc",
            relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
        )
        self.hotkey_entry.place(x=58, y=34, width=52, height=22)
        self.hotkey_hint = tk.Label(
            self.panel, text="可修改后再开始", bg="#111827", fg="#64748b",
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.hotkey_hint.place(x=118, y=34, width=120, height=22)

        self.action = tk.Button(
            self.panel, text="模型预热中…", state="disabled", command=self.toggle,
            relief="flat", bd=0, bg="#334155", activebackground="#475569",
            fg="white", activeforeground="white", disabledforeground="#cbd5e1",
            font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2",
        )
        self.action.place(x=12, y=62, width=228, height=38)
        self.opacity_label = tk.Label(
            self.panel, text="透明度 48%", bg="#111827", fg="#94a3b8",
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.opacity_label.place(x=12, y=105, width=76, height=22)
        self.opacity_var = tk.IntVar(value=48)
        self.opacity_scale = tk.Scale(
            self.panel, from_=15, to=85, orient="horizontal", resolution=1,
            showvalue=False, variable=self.opacity_var, command=self.change_opacity,
            bg="#111827", fg="#cbd5e1", troughcolor="#334155",
            activebackground="#60a5fa", highlightthickness=0, bd=0,
            sliderlength=14, length=148,
        )
        self.opacity_scale.place(x=88, y=102, width=152, height=30)
        self.status_dot = tk.Label(self.panel, text="●", bg="#111827", fg="#60a5fa", font=("Segoe UI", 9))
        self.status_dot.place(x=12, y=146, width=16, height=18)
        self.status = tk.Label(
            self.panel, text="预热模板，不抓取屏幕", bg="#111827", fg="#94a3b8",
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.status.place(x=30, y=146, width=210, height=18)

        for widget in (self.panel, self.title, self.status):
            widget.bind("<ButtonPress-1>", self.begin_drag)
            widget.bind("<B1-Motion>", self.drag)
        self.root.bind("<Button-3>", self.show_menu)
        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="退出", command=self.close)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        if exclude_capture:
            self.root.after(80, self.exclude_from_capture)
        self.root.after(100, self.poll_events)
        threading.Thread(target=self.preload, daemon=True).start()

    def exclude_from_capture(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.root.winfo_id(), 2)
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00)
        except Exception:
            pass

    def hide_overlay(self) -> None:
        if self.overlay_window is not None:
            self.overlay_window.withdraw()

    def change_opacity(self, value: str) -> None:
        percent = max(15, min(85, round(float(value))))
        self.overlay_opacity = percent / 100.0
        self.opacity_label.configure(text=f"透明度 {percent}%")
        if self.overlay_window is not None:
            try:
                self.overlay_window.attributes("-alpha", self.overlay_opacity)
            except tk.TclError:
                pass

    def show_overlay(self, path: str, bounds: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = bounds
        if self.overlay_window is None:
            window = tk.Toplevel(self.root)
            window.overrideredirect(True)
            window.configure(bg="#000000")
            window.attributes("-topmost", True)
            window.attributes("-alpha", self.overlay_opacity)
            try:
                window.wm_attributes("-transparentcolor", "#000000")
            except tk.TclError:
                pass
            label = tk.Label(window, bd=0, highlightthickness=0, bg="#000000")
            label.pack(fill="both", expand=True)
            self.overlay_window, self.overlay_label = window, label
            window.update_idletasks()
            try:
                hwnd = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)
                exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, -20, exstyle | 0x00080000 | 0x00000020 | 0x08000000
                )
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00)
            except Exception:
                pass
        self.overlay_photo = tk.PhotoImage(file=path)
        self.overlay_label.configure(image=self.overlay_photo)
        try:
            self.overlay_window.attributes("-alpha", self.overlay_opacity)
        except tk.TclError:
            pass
        self.overlay_window.geometry(f"{x1 - x0}x{y1 - y0}+{x0}+{y0}")
        self.overlay_window.deiconify()
        self.overlay_window.lift()

    def begin_drag(self, event) -> None:
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag(self, event) -> None:
        x = event.x_root - self.drag_origin[0]
        y = event.y_root - self.drag_origin[1]
        self.root.geometry(f"+{x}+{y}")

    def show_menu(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def preload(self) -> None:
        try:
            started = time.perf_counter()
            self.recognizer = build_recognizer()
            self.ready.set()
            self.events.put({"kind": "ready", "initMs": (time.perf_counter() - started) * 1000})
        except Exception as error:
            self.events.put({"kind": "fatal", "message": f"模型加载失败：{error}"})

    def toggle(self) -> None:
        if self.running:
            self.stop_event.set()
            self.action.configure(state="disabled", text="正在停止…", bg="#475569")
            return
        try:
            self.hotkey_name, self.hotkey_vk = hotkey_to_vk(self.hotkey_var.get())
        except ValueError as error:
            self.status.configure(text=str(error), fg="#ff8a65")
            self.status_dot.configure(fg="#ff8a65")
            return
        self.running = True
        self.stop_event.clear()
        self.hotkey_entry.configure(state="disabled")
        self.action.configure(text="停止实机测试", bg="#dc3545", activebackground="#b91c1c")
        self.status.configure(text="正在绑定游戏窗口…", fg="#cbd5e1")
        self.status_dot.configure(fg="#60a5fa")
        self.worker = threading.Thread(target=self.test_loop, daemon=True)
        self.worker.start()

    def test_loop(self) -> None:
        self.ready.wait()
        if self.recognizer is None or self.stop_event.is_set():
            self.events.put({"kind": "stopped"})
            return
        self.recognizer.reset_session()
        try:
            hwnd, pid, _ = find_game_window()
        except Exception:
            self.events.put({"kind": "error", "message": "未找到第五人格窗口，进入对局后再点"})
            return

        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        session = datetime.now().strftime("%Y%m%d-%H%M%S")
        event_path = OUTPUT_ROOT / f"events-{session}.jsonl"
        sample_dir = OUTPUT_ROOT / f"samples-{session}"
        append_event(event_path, {
            "time": now_iso(), "event": "session-start", "pid": pid,
            "hotkey": self.hotkey_name,
            "policy": "仅轮询指定地图键；打开事件后只读完整游戏地图；每次完成识别仅保存一张完整地图证据；无键鼠注入",
        })
        self.events.put({"kind": "listening", "hotkey": self.hotkey_name})
        trigger_state = {"generation": 0, "mapOpen": False, "presses": 0, "captures": 0}
        trigger_lock = threading.Lock()
        capture_threads: list[threading.Thread] = []
        alignment_lock = threading.Lock()
        alignment_history: dict[tuple[str, int], list[dict]] = {}
        saved_candidate_keys: set[tuple[str, int]] = set()

        def still_open(generation: int) -> bool:
            with trigger_lock:
                return (
                    not self.stop_event.is_set()
                    and trigger_state["generation"] == generation
                    and trigger_state["mapOpen"]
                )

        def capture_open_map(
            generation: int, press_number: int, pressed_at: float
        ) -> None:
            if self.stop_event.wait(MAP_OPEN_SETTLE_SECONDS) or not still_open(generation):
                return
            last_interface: dict = {"detected": False, "reason": "not-captured"}
            bounds = None
            capture_number = 0
            for attempt in range(1, MAP_CAPTURE_RETRIES + 1):
                if not still_open(generation):
                    return
                frame, bounds = capture_client(hwnd)
                with trigger_lock:
                    trigger_state["captures"] += 1
                    capture_number = trigger_state["captures"]
                last_interface = detect_map_interface(frame)
                append_event(event_path, {
                    "time": now_iso(), "event": "map-capture-attempt",
                    "press": press_number, "attempt": attempt,
                    "capture": capture_number,
                    "interfaceDetected": bool(last_interface.get("detected")),
                })
                if last_interface.get("detected"):
                    floor_state = detect_selected_floor(frame)
                    started = time.perf_counter()
                    # The generic dark-backdrop UI detector can become true a
                    # few frames before the map itself is drawn. Require the
                    # native-scale exit icon before consuming a valid frame;
                    # this keeps transition/gameplay images out of the strict
                    # first-two evidence window. Unusual icon scales remain an
                    # offline fallback, not a live latency penalty.
                    anchors = self.recognizer.find_anchors(
                        frame, allow_unusual_scales=False
                    )
                    if not anchors:
                        append_event(event_path, {
                            "time": now_iso(), "event": "map-anchor-not-ready",
                            "press": press_number, "attempt": attempt,
                            "capture": capture_number,
                            "probeMs": (time.perf_counter() - started) * 1000,
                        })
                        if attempt < MAP_CAPTURE_RETRIES:
                            if self.stop_event.wait(MAP_CAPTURE_RETRY_SECONDS):
                                return
                            continue
                        break
                    result = self.recognizer.recognize(
                        frame, floor_state.get("selectedFloor"), update_session=False,
                        precomputed_anchors=anchors,
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    map_still_open = still_open(generation)
                    consensus = commit_open_result(
                        self.recognizer, result,
                        floor_state.get("selectedFloor"), map_still_open,
                    )
                    if consensus is None:
                        # A result that finishes after the closing G press is
                        # neither shown nor allowed to consume one of the two
                        # valid identity frames.
                        append_event(event_path, {
                            "time": now_iso(), "event": "recognition-discarded-map-closed",
                            "press": press_number, "capture": capture_number,
                            "recognitionMs": elapsed_ms,
                            "decisionFromKeyMs": (time.perf_counter() - pressed_at) * 1000,
                        })
                        return
                    result["sessionConsensus"] = consensus
                    marked = result.get("markedEvidence") or {}
                    decision_from_key_ms = (time.perf_counter() - pressed_at) * 1000
                    state, summary_text, color = result_summary(result)
                    # Put the decision on the UI queue before PNG encoding and
                    # audit persistence. Full-resolution evidence must never
                    # delay the result the player sees.
                    self.events.put({
                        "kind": "result", "state": state, "text": summary_text,
                        "color": color, "ms": elapsed_ms,
                        "decisionFromKeyMs": decision_from_key_ms,
                        "overlayPath": None, "bounds": bounds,
                    })
                    append_event(event_path, {
                        "time": now_iso(), "event": "decision-ready",
                        "press": press_number, "capture": capture_number,
                        "recognitionMs": elapsed_ms,
                        "decisionFromKeyMs": decision_from_key_ms,
                        "reason": result.get("reason"),
                        "mapId": result.get("mapId"),
                        "success": bool(result.get("success")),
                    })
                    sample_dir.mkdir(parents=True, exist_ok=True)
                    sample_path = sample_dir / (
                        f"press-{press_number:03d}-{result.get('mapId') or 'none'}-"
                        f"{marked.get('mapId') or 'none'}.png"
                    )
                    if not save_map_sample(frame, sample_path):
                        sample_path = None
                    overlay_path = None
                    candidate_preview_path = None
                    overlay_meta = {"success": False, "reason": "recognition-unaccepted"}
                    if result.get("success") and map_still_open:
                        map_id = result.get("mapId", "")
                        floor = result.get("floor")
                        overlay, multipoint_meta = build_multipoint_overlay(
                            frame, map_id, floor,
                            route_family="speed", opacity=1.0,
                        )
                        overlay_meta = {
                            "engine": "multipoint",
                            **multipoint_meta,
                            "presentationOpacity": self.overlay_opacity,
                            "routeFamiliesShareGeometry": True,
                        }
                        if overlay is not None and not overlay_meta.get("displayAllowed"):
                            # A new partial-map candidate must not displace an
                            # older independently verified live profile. Today
                            # this preserves map-24 while other unverified maps
                            # still follow the two-fit candidate gate below.
                            verified_overlay, verified_meta = build_aligned_overlay(
                                frame.shape, map_id, floor,
                                result.get("anchor"), opacity=1.0,
                            )
                            if verified_overlay is not None:
                                overlay = verified_overlay
                                overlay_meta = {
                                    "engine": "legacy-verified-preferred",
                                    "multipointAttempt": multipoint_meta,
                                    "legacy": verified_meta,
                                    "success": True,
                                    "reason": "existing-verified-live-profile-preferred",
                                    "displayAllowed": True,
                                    "presentationOpacity": self.overlay_opacity,
                                    "routeFamiliesShareGeometry": True,
                                }
                        if overlay is not None:
                            display_allowed = bool(overlay_meta.get("displayAllowed"))
                            stable_candidate = False
                            candidate_key = (map_id, int(floor))
                            if not display_allowed:
                                with alignment_lock:
                                    accumulated = accumulate_alignment_evidence(
                                        alignment_history, map_id, int(floor), overlay_meta
                                    )
                                    stable_candidate = bool(
                                        accumulated["stableForReview"]
                                        and candidate_key not in saved_candidate_keys
                                    )
                                    if stable_candidate:
                                        saved_candidate_keys.add(candidate_key)
                                overlay_meta["sessionEvidence"] = accumulated
                            if display_allowed or stable_candidate:
                                overlay_path = sample_dir / (
                                    f"press-{press_number:03d}-{map_id}-f{floor}-"
                                    "multipoint-overlay.png"
                                )
                                if not imwrite_unicode(overlay_path, overlay):
                                    overlay_path = None
                            if (
                                overlay_path is not None
                                and display_allowed
                                and still_open(generation)
                            ):
                                self.events.put({
                                    "kind": "overlay-ready",
                                    "overlayPath": str(overlay_path.resolve()),
                                    "bounds": bounds,
                                })
                            elif overlay_path is not None and stable_candidate:
                                candidate_preview_path = sample_dir / (
                                    f"press-{press_number:03d}-{map_id}-f{floor}-"
                                    "多点对齐候选预览.png"
                                )
                                preview = build_overlay_preview(
                                    frame, overlay, self.overlay_opacity
                                )
                                if not imwrite_unicode(candidate_preview_path, preview):
                                    candidate_preview_path = None
                                else:
                                    overlay_meta["candidateSaved"] = True
                                    self.events.put({
                                        "kind": "alignment-candidate-saved",
                                        "mapId": map_id,
                                        "floor": floor,
                                        "previewPath": str(candidate_preview_path.resolve()),
                                    })
                        else:
                            # Preserve the two existing truth-verified map-24
                            # profiles if the current frame cannot support the
                            # distributed-point fit. The legacy registry itself
                            # is fail-closed for every unverified map.
                            legacy_overlay, legacy_meta = build_aligned_overlay(
                                frame.shape, map_id, floor,
                                result.get("anchor"), opacity=1.0,
                            )
                            overlay_meta = {
                                "engine": "legacy-verified-fallback",
                                "multipointAttempt": multipoint_meta,
                                "legacy": legacy_meta,
                                "success": bool(legacy_overlay is not None),
                                "reason": legacy_meta.get("reason"),
                                "presentationOpacity": self.overlay_opacity,
                                "routeFamiliesShareGeometry": True,
                            }
                            if legacy_overlay is not None:
                                overlay_path = sample_dir / (
                                    f"press-{press_number:03d}-{map_id}-f{floor}-"
                                    "verified-legacy-overlay.png"
                                )
                                if not imwrite_unicode(overlay_path, legacy_overlay):
                                    overlay_path = None
                                elif still_open(generation):
                                    self.events.put({
                                        "kind": "overlay-ready",
                                        "overlayPath": str(overlay_path.resolve()),
                                        "bounds": bounds,
                                    })
                    payload = {
                        "time": now_iso(), "session": session, "press": press_number,
                        "state": "recognized", "recognitionMs": elapsed_ms,
                        "decisionFromKeyMs": decision_from_key_ms,
                        "mapStillOpenAtResult": map_still_open,
                        "clientBounds": bounds, "mapInterface": last_interface,
                        "floorState": floor_state,
                        "mapSample": str(sample_path) if sample_path else None,
                        "recognition": serializable(result),
                        "overlay": serializable(overlay_meta),
                        "overlayPath": str(overlay_path) if overlay_path else None,
                        "candidatePreviewPath": (
                            str(candidate_preview_path) if candidate_preview_path else None
                        ),
                    }
                    LATEST_STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    append_event(event_path, {
                        "time": payload["time"], "event": "recognition",
                        "press": press_number, "capture": capture_number,
                        "mapStillOpenAtResult": map_still_open,
                        "selectedFloor": floor_state.get("selectedFloor"),
                        "effectiveFloor": result.get("floor"),
                        "floorSource": result.get("floorSource"),
                        "floorTransition": (result.get("sessionConsensus") or {}).get("floorTransition"),
                        "recognitionMs": elapsed_ms,
                        "decisionFromKeyMs": decision_from_key_ms,
                        "reason": result.get("reason"), "baseCandidate": result.get("mapId"),
                        "markedCandidate": marked.get("mapId"), "success": bool(result.get("success")),
                        "mapSample": payload["mapSample"],
                    })
                    return
                if attempt < MAP_CAPTURE_RETRIES and self.stop_event.wait(MAP_CAPTURE_RETRY_SECONDS):
                    return
            payload = {
                "time": now_iso(), "session": session, "press": press_number,
                "state": "map-not-confirmed", "clientBounds": bounds,
                "mapInterface": last_interface,
            }
            LATEST_STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            append_event(event_path, {
                "time": payload["time"], "event": "map-not-confirmed",
                "press": press_number, "attempts": MAP_CAPTURE_RETRIES,
            })
            self.events.put({"kind": "map-not-confirmed", "hotkey": self.hotkey_name})

        def guarded_capture_open_map(
            generation: int, press_number: int, pressed_at: float
        ) -> None:
            try:
                capture_open_map(generation, press_number, pressed_at)
            except Exception as error:
                append_event(event_path, {
                    "time": now_iso(), "event": "map-capture-error",
                    "press": press_number, "message": str(error),
                })
                self.events.put({"kind": "capture-error", "message": f"本次截取失败：{error}"})

        key_was_down = bool(ctypes.windll.user32.GetAsyncKeyState(self.hotkey_vk) & 0x8000)
        try:
            while not self.stop_event.is_set():
                key_is_down = bool(ctypes.windll.user32.GetAsyncKeyState(self.hotkey_vk) & 0x8000)
                if key_is_down and not key_was_down:
                    pressed_at = time.perf_counter()
                    with trigger_lock:
                        trigger_state["presses"] += 1
                        trigger_state["generation"] += 1
                        trigger_state["mapOpen"] = not trigger_state["mapOpen"]
                        press_number = trigger_state["presses"]
                        generation = trigger_state["generation"]
                        map_open = trigger_state["mapOpen"]
                    append_event(event_path, {
                        "time": now_iso(), "event": "map-key-pressed",
                        "press": press_number, "mapExpectedOpen": map_open,
                    })
                    if map_open:
                        self.events.put({"kind": "map-opening", "hotkey": self.hotkey_name})
                        capture_thread = threading.Thread(
                            target=guarded_capture_open_map,
                            args=(generation, press_number, pressed_at), daemon=True,
                        )
                        capture_threads.append(capture_thread)
                        capture_thread.start()
                    else:
                        self.events.put({"kind": "map-closed", "hotkey": self.hotkey_name})
                key_was_down = key_is_down
                self.stop_event.wait(0.015)
        except Exception as error:
            self.events.put({"kind": "error", "message": f"测试中断：{error}"})
        finally:
            for capture_thread in capture_threads:
                capture_thread.join(timeout=1.5)
            append_event(event_path, {
                "time": now_iso(), "event": "session-stop",
                "mapKeyPresses": trigger_state["presses"],
                "captures": trigger_state["captures"],
            })
            self.events.put({"kind": "stopped"})

    def poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event["kind"]
                if kind == "ready" and not self.running:
                    self.action.configure(state="normal", text="开始实机测试", bg="#16a34a", activebackground="#15803d")
                    self.status.configure(text=f'待机 · 预热 {event["initMs"] / 1000:.1f}s · 地图键 G', fg="#94a3b8")
                    self.status_dot.configure(fg="#3ddc84")
                elif kind == "listening":
                    self.status.configure(text=f'测试中 · 按 {event["hotkey"]} 打开/关闭地图', fg="#cbd5e1")
                    self.status_dot.configure(fg="#60a5fa")
                elif kind == "map-opening":
                    self.hide_overlay()
                    self.status.configure(text=f'检测到 {event["hotkey"]} · 等待地图展开', fg="#cbd5e1")
                    self.status_dot.configure(fg="#60a5fa")
                elif kind == "map-closed":
                    self.hide_overlay()
                    self.status.configure(text=f'地图已关闭 · 等待下次 {event["hotkey"]}', fg="#94a3b8")
                    self.status_dot.configure(fg="#60a5fa")
                elif kind == "map-not-confirmed":
                    self.status.configure(text=f'未确认地图画面 · 再按 {event["hotkey"]} 重开', fg="#ffb84d")
                    self.status_dot.configure(fg="#ffb84d")
                elif kind == "capture-error":
                    self.status.configure(text=event["message"], fg="#ff8a65")
                    self.status_dot.configure(fg="#ff8a65")
                elif kind == "result":
                    self.status.configure(text=f'{event["text"]} · {event["ms"]:.0f}ms', fg=event["color"])
                    self.status_dot.configure(fg=event["color"])
                    if event.get("overlayPath") and event.get("bounds"):
                        self.show_overlay(event["overlayPath"], tuple(event["bounds"]))
                elif kind == "overlay-ready":
                    if event.get("overlayPath") and event.get("bounds"):
                        self.show_overlay(event["overlayPath"], tuple(event["bounds"]))
                elif kind == "alignment-candidate-saved":
                    self.status.configure(
                        text=f'{event["mapId"]} · {event["floor"]}楼多点候选已保存',
                        fg="#ffd166",
                    )
                    self.status_dot.configure(fg="#ffd166")
                elif kind in {"error", "fatal"}:
                    self.running = False
                    self.stop_event.set()
                    self.hotkey_entry.configure(state="normal")
                    self.action.configure(state="normal" if kind == "error" else "disabled", text="重新开始" if kind == "error" else "不可用", bg="#b45309")
                    self.status.configure(text=event["message"], fg="#ff8a65")
                    self.status_dot.configure(fg="#ff8a65")
                elif kind == "stopped":
                    self.hide_overlay()
                    self.running = False
                    self.hotkey_entry.configure(state="normal")
                    self.action.configure(state="normal", text="开始实机测试", bg="#16a34a", activebackground="#15803d")
                    if not self.status.cget("text").startswith(("未找到", "测试中断")):
                        self.status.configure(text="已停止 · 不再抓取屏幕", fg="#94a3b8")
                    self.status_dot.configure(fg="#3ddc84")
        except queue.Empty:
            pass
        # While testing, keep UI-queue latency below one display frame or two.
        # The idle window can poll slowly without affecting capture latency.
        self.root.after(ACTIVE_UI_POLL_MS if self.running else 120, self.poll_events)

    def close(self) -> None:
        self.stop_event.set()
        self.hide_overlay()
        self.root.after(150, self.root.destroy)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="第五人格地图鉴别实机测试悬浮按钮")
    parser.add_argument("--self-test", type=Path, help="用指定截图进行无界面自检")
    parser.add_argument("--allow-capture", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(args.self_test), ensure_ascii=False, indent=2))
        return
    # Prevent accidental double-clicks from creating multiple capture loops.
    mutex_name = "Local\\IdentityVMapRecognitionTestButtonCaptureTest" if args.allow_capture else "Local\\IdentityVMapRecognitionTestButton"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    root = tk.Tk()
    FloatingTestButton(root, exclude_capture=not args.allow_capture)
    root.mainloop()
    if mutex:
        ctypes.windll.kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
