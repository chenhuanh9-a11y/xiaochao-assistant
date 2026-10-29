"""Display-source wording and bounded, UI-owned fallback notification state.

No resource lookup, rendering, recognition, timers or UI work occurs here. The
caller must supply the *committed* layer kind and separately classified author
availability; an absent author asset is not a registration or decoding failure.
"""
from dataclasses import dataclass
from typing import Hashable


@dataclass(frozen=True)
class DisplayFeedback:
    title: str
    status: str
    success: str
    notice: str = ""


def display_feedback(display_kind: str, author_status: str = "none") -> DisplayFeedback:
    """Describe actual pixels, never infer availability from a selected profile.

    author_status: none, missing, registration-failed or load-error. Unknown
    values deliberately cannot produce the one-time missing-route notification.
    """
    if display_kind == "author-route":
        return DisplayFeedback("作者路线已核对", "当前显示作者路线", "已识别并贴合作者路线")
    if display_kind == "custom-route":
        return DisplayFeedback("自定义地图已核对", "当前显示自定义地图", "已识别并贴合自定义地图")
    if display_kind not in {"base-map", "shared-structure"}:
        return DisplayFeedback("显示尚未就绪", "当前地图显示尚未就绪", "")
    label = "基础地图" if display_kind == "base-map" else "结构底图"
    notice = ""
    if author_status == "missing":
        status = f"所选作者没有当前地图／楼层的可用路线，当前显示{label}。"
        notice = f"此图暂无作者路线，已显示{label}"
    elif author_status == "registration-failed":
        status = f"所选作者该层配准未通过，当前显示{label}。"
    elif author_status == "load-error":
        status = f"所选作者该层图片读取失败，当前显示{label}。"
    else:
        status = f"当前显示{label}（无作者路线）"
    return DisplayFeedback(f"{label}已核对", status, f"已识别并贴合{label}", notice)


@dataclass(frozen=True)
class DisplayNoticeContext:
    """Caller-owned identity including epochs used by the real display commit."""
    round_id: Hashable
    mode: str
    map_id: str
    floor: str
    resource_generation: Hashable
    frame_generation: Hashable


class FallbackNoticeTracker:
    """One missing-route notice per mode/map/floor in the active round.

    UI-thread only. Call begin_round at an actual round boundary, not every G
    reopen, resource import or author switch. It keeps only the active round.
    The caller owns visibility/current-commit validation, and must pass the
    captured and current contexts for a second stale-result check here.
    """
    def __init__(self):
        self._round_id = None
        self._seen = set()

    def begin_round(self, round_id: Hashable) -> None:
        if round_id != self._round_id:
            self._round_id = round_id
            self._seen.clear()

    def end_round(self) -> None:
        self._round_id = None
        self._seen.clear()

    def consume(self, captured: DisplayNoticeContext, current: DisplayNoticeContext,
                *, display_kind: str, author_status: str, current_commit: bool) -> str:
        if (not current_commit or captured != current or self._round_id is None
                or current.round_id != self._round_id or not current.map_id
                or not current.mode or not current.floor):
            return ""
        message = display_feedback(display_kind, author_status).notice
        if not message:
            return ""
        key = (current.mode, current.map_id, current.floor)
        if key in self._seen:
            return ""
        self._seen.add(key)
        return message
