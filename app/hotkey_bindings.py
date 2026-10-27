"""Pure shortcut parsing/matching; no hooks, windows, IO or registration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


MODIFIERS = (("Ctrl", 0x11), ("Alt", 0x12), ("Shift", 0x10))
_ALIASES = {"CTRL": "Ctrl", "CONTROL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}


@dataclass(frozen=True)
class Chord:
    name: str
    vk: int
    modifiers: tuple[int, ...] = ()

    @property
    def identity(self) -> tuple[int, tuple[int, ...]]:
        return self.vk, self.modifiers


def parse_chord(value: str, resolve_primary: Callable[[str], tuple[str, int]]) -> Chord:
    raw = str(value).strip()
    if not raw:
        return Chord("", 0)
    # Preserve existing aliases for punctuation such as '+'.
    try:
        name, vk = resolve_primary(raw)
    except ValueError:
        parts = [part.strip() for part in raw.split("+")]
        if len(parts) < 2 or any(not part for part in parts):
            raise ValueError("无效组合键；请重新录制一个主键，可搭配 Ctrl、Alt、Shift") from None
        modifiers = []
        for part in parts[:-1]:
            canonical = _ALIASES.get(part.upper())
            if canonical is None or canonical in modifiers:
                raise ValueError("修饰键仅支持 Ctrl、Alt、Shift，且不能重复")
            modifiers.append(canonical)
        name, vk = resolve_primary(parts[-1])
        if vk in {0x10, 0x11, 0x12, 0x5B, 0x5C}:
            raise ValueError("请录制一个主键，不能只使用修饰键")
        ordered = tuple(key for label, key in MODIFIERS if label in modifiers)
        labels = [label for label, key in MODIFIERS if key in ordered]
        return Chord("+".join((*labels, name)), vk, ordered)
    if vk in {0x5B, 0x5C}:
        raise ValueError("暂不支持 Windows 修饰键")
    return Chord(name, vk)


def modifiers_match(chord: Chord, read: Callable[[int], int], *, reject_released: bool = False) -> bool:
    if not chord.vk:
        return False
    samples = {vk: int(read(vk)) & 0xFFFF for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)}
    if any(samples[vk] & (0x8001 if reject_released else 0x8000) for vk in (0x5B, 0x5C)):
        return False
    # An async low-bit modifier tap and primary edge can have occurred in
    # either order between polls. Never downgrade this unknown chord to bare.
    # GetKeyState's local event path has no async pressed-since-read latch.
    if reject_released and any(vk != chord.vk and samples[vk] & 1 and not samples[vk] & 0x8000
                               for _label, vk in MODIFIERS):
        return False
    return all(vk == chord.vk or bool(samples[vk] & 0x8000) == (vk in chord.modifiers)
               for _label, vk in MODIFIERS)


def primary_state(chord: Chord, read: Callable[[int], int]) -> int:
    return (int(read(chord.vk)) & 0xFFFF) if chord.vk else 0


def validate_unique(bindings: dict[str, Chord]) -> None:
    seen = {}
    for target, chord in bindings.items():
        if not chord.vk:
            continue
        if chord.identity in seen:
            raise ValueError(f"与 {seen[chord.identity]} 的快捷键重复，请换一个组合")
        seen[chord.identity] = target
    # A bare modifier fires before a later primary is knowable to a polling
    # listener. Reject this application conflict instead of firing both.
    bare = {chord.vk: target for target, chord in bindings.items()
            if chord.vk in {key for _label, key in MODIFIERS} and not chord.modifiers}
    for target, chord in bindings.items():
        if any(key in bare for key in chord.modifiers):
            other = next(bare[key] for key in chord.modifiers if key in bare)
            raise ValueError(f"与 {other} 的单独修饰键冲突，请改用不同组合")
