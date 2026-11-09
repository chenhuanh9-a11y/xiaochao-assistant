"""Read actual Python method bodies, simulate levels/windows/clock, never start hooks or Qt UI.

The only substituted dependencies are QObject/Signal/mailbox construction, OS levels/window facts,
and the source clock. Backend _key_event and source state/mouse functions execute unchanged.
Run from any directory; output is local to this test project, never player settings/resources.
"""
from __future__ import annotations
import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace, MethodType
from unittest.mock import patch
import os
import time

ROOT = Path(__file__).resolve().parents[3]
QT = ROOT / "experiments" / "qt_quick"
sys.path.insert(0, str(ROOT / "app"))

class Signal:
    def __init__(self, *args): pass
    def emit(self, *args): pass
class QObject:
    def __init__(self, *args): pass

qtcore = ModuleType("PySide6.QtCore")
qtcore.QObject, qtcore.Signal = QObject, Signal
sys.modules["PySide6"] = ModuleType("PySide6")
sys.modules["PySide6.QtCore"] = qtcore
mailbox = ModuleType("client_map_input")
mailbox.PointerEventMailbox = type("PointerEventMailbox", (), {})
sys.modules["client_map_input"] = mailbox

spec = importlib.util.spec_from_file_location("client_native_keys", QT / "client_native_keys.py")
source_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = source_module
spec.loader.exec_module(source_module)
from hotkey_bindings import Chord, parse_chord, modifiers_match, validate_unique
from hotkey_keys import _primary_hotkey_to_vk, NATIVE_ONLY_SHORTCUT_KEYS

def production_function(path, class_name, function_name, globals_):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name) if class_name else tree
    node = next(node for node in parent.body if isinstance(node, ast.FunctionDef) and node.name == function_name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), globals_)
    return globals_[function_name]

GLOBALS = dict(os=os, time=time, modifiers_match=modifiers_match, MANUAL_FLOOR_HOLD_SECONDS=2.,
    PICKER_MODIFIER_VKS=source_module.MODIFIER_KEYS,
    NATIVE_ONLY_SHORTCUT_KEYS=NATIVE_ONLY_SHORTCUT_KEYS,
    MANAGEMENT_ACTIONS=frozenset({"toggle-panel", "toggle-sheet-visibility", "start-round", "toggle-round", "end-round"}))
GLOBALS["global_key_is_allowed"] = production_function(ROOT / "app" / "session_state_machine.py", None, "global_key_is_allowed", {})
key_event = production_function(QT / "client_input.py", "NativeShortcutBackend", "_key_event", GLOBALS)

def key(vk, down=True, created=99., foreground=42, scan=0, flags=0, **extra):
    return dict(kind="key", vk=vk, down=down, created=created, foreground=foreground, scan=scan, flags=flags, **extra)
def mouse(flags): return dict(kind="mouseActivity", flags=flags)
def case(name, bindings, steps, **extra): return dict(name=name, bindings=bindings, steps=steps, **extra)

cases = [
    case("fresh-repeat-release", {"map":"G"}, [key(71), key(71), key(71,False), key(71)]),
    case("held-before-subscribe", {"map":"G"}, [key(71),key(71,False),key(71)], initial=[71]),
    case("left-right-modifier-seeding", {"map":"G"}, [key(0xa2,False),key(71),key(71,False),key(0x11,False,flags=1),key(71)], initial=[0x11,0xa2,0xa3]),
    case("generic-physical-ctrl-chord", {"map":"Ctrl+G"}, [key(0x11),key(71),key(71,False),key(0xa2,False),key(71)]),
    case("windows-and-extra-modifier-rejected", {"map":"Ctrl+G"}, [key(0x11),key(0x10),key(71),key(71,False),key(0x10,False),key(0x5b),key(71)]),
    case("game-scope-and-current-focus", {"map":"G"}, [key(71,foreground=77),key(71,False),key(71,scope={"currentForeground":77}),key(71,False),key(71,scope={"currentForeground":42})]),
    case("management-while-game-unavailable", {"toggle-sheet-visibility":"MOUSE5"}, [key(6,foreground=77,scope={"gameValid":False,"currentForeground":77}),key(6,False,foreground=77),key(6,foreground=77,scope={"assistant":True})]),
    case("panel-local-owner", {"toggle-panel":"F9"}, [key(0x78,foreground=77,scope={"assistant":True,"currentForeground":77}),key(0x78,False),key(0x78,foreground=77,scope={"assistant":False})]),
    case("panel-mouse-native-owner", {"toggle-panel":"MOUSE4"}, [key(5,foreground=77,scope={"assistant":True,"currentForeground":77}),key(5),key(5,False)]),
    case("panel-oem-native-owner", {"toggle-panel":"OEM_1"}, [key(0xba,foreground=77,scope={"assistant":True,"currentForeground":77})]),
    case("stale-release-does-not-stick", {"map":"G"}, [key(71,created=97.),key(71,False,created=97.),key(71,created=98.)]),
    case("future-nan-infinity", {"toggle-panel":"MOUSE4"}, [key(5,created=100.01),key(5,False),key(5,created="nan"),key(5,False),key(5,created="inf"),key(5,False),key(5)]),
    case("text-editing-cleans-levels", {"map":"G"}, [key(71,scope={"text":True}),key(71,False),key(71,scope={"text":False,"editing":True}),key(71,False),key(71,scope={"editing":False})]),
    case("resume-boundary", {"map":"G"}, [key(71,created=99.,scope={"after":99.5}),key(71,False),key(71,created=99.5)]),
    case("bare-shift-clean-inclusive-tap", {"map":"SHIFT"}, [key(0x10,created=99.,scan=0x36),key(0xa1,False,created=99.5),key(0xa0,created=99.6),key(0x10,False,created=100.11)]),
    case("bare-ctrl-unsubscribed-key-blocks", {"map":"CTRL"}, [key(0x11,created=99.),key(65,created=99.1),key(65,False,created=99.2),key(0xa2,False,created=99.3)]),
    case("bare-alt-wheel-and-click-blocks", {"map":"ALT"}, [key(0x12,created=99.),mouse(0x400),key(0xa4,False,created=99.1),key(0x12,created=99.2),mouse(1),mouse(2),key(0x12,False,created=99.3)]),
    case("bare-modifier-needs-mouse", {"map":"CTRL"}, [key(0x11),key(0x11,False,created=99.2)], mouseAvailable=False),
    case("bare-modifier-focus-change", {"map":"CTRL"}, [key(0x11,created=99.),key(0x11,False,created=99.2,foreground=77)]),
    case("bare-modifier-edit-transition", {"map":"CTRL"}, [key(0x11),key(0x11,False,created=99.2,scope={"editing":True}),key(0x11,created=99.4,scope={"editing":False}),key(0x11,False,created=99.5)]),
    case("picker-priority-and-escape", {"candidate-prev":"F1"}, [key(0x70),key(0x70,False),key(0x1b),key(0x1b,False),key(0x11),key(0x70)], picker=["F1","Escape"]),
    case("picker-other-app-rejected", {"candidate-next":"F2"}, [key(0x71,foreground=77,scope={"currentForeground":77})], picker=["F2"]),
    case("bare-modifier-resume-rejects-old-press", {"map":"CTRL"}, [key(0x11,created=99.),key(0x11,False,created=99.2,scope={"after":99.1})]),
]

for item in cases:
    source = source_module.PassThroughKeyboardSource()
    chords = {action:parse_chord(value,_primary_hotkey_to_vk) for action,value in item["bindings"].items()}
    chords["escape"] = Chord("ESCAPE",0x1b)
    validate_unique(chords)
    source.update_keys([*(chord.vk for chord in chords.values() if chord.vk),0x70,0x71,0x72,0x73,0x21,0x22,8,0x1b])
    levels = set(item.get("initial", []))
    source._user = SimpleNamespace(GetAsyncKeyState=lambda vk:0x8000 if vk in levels else 0)
    source._held = source_module.initial_pressed_keys(source._keys, source._user.GetAsyncKeyState)
    source._tap_pressed = source_module.initial_pressed_keys(range(1,256),source._user.GetAsyncKeyState) if source._bare_modifiers else set()
    source.pointer_installed = item.get("mouseAvailable", True)
    item["initialHeld"] = sorted(source.held)
    scope = dict(currentForeground=42,gameValid=True,assistant=False,gameEnabled=True,managementEnabled=True,text=False,editing=False,after=0.)
    actions, picker = [], []
    native = SimpleNamespace(is_window=lambda hwnd:scope["gameValid"] and hwnd==42,
        foreground_window=lambda:scope["currentForeground"],window_process_id=lambda hwnd:os.getpid() if scope["assistant"] else -1)
    backend = SimpleNamespace(_closed=False,_source=source,_event_down=set(source.held),_modifier_presses={},_text=False,_editing=False,
        _bindings=chords,_hwnd=42,_enabled=True,_management_enabled=True,_game_events_after=0.,_native=native,
        _picker_keys={name:{"F1":0x70,"F2":0x71,"Escape":0x1b}[name] for name in item.get("picker",[])},
        pickerKeyRequested=SimpleNamespace(emit=picker.append),_dispatch_action=lambda action,event:actions.append(action),
        _hook_failed=lambda error:(_ for _ in ()).throw(RuntimeError(error)))
    for step in item["steps"]:
        actions.clear();picker.clear()
        if step["kind"] == "mouseActivity":
            source._modifier_mouse_activity(step["flags"])
            continue
        previous = scope.copy();scope.update(step.get("scope",{}))
        if any(previous[key] != scope[key] for key in ("text","editing")):
            source.cancel_modifier_taps();backend._modifier_presses.clear()
        backend._text,backend._editing = scope["text"],scope["editing"]
        backend._enabled,backend._management_enabled = scope["gameEnabled"],scope["managementEnabled"]
        backend._game_events_after=scope["after"]
        source_created = step["created"] if isinstance(step["created"],(int,float)) else 99.
        with patch("time.monotonic",return_value=source_created):
            event=source._transition_event(step["vk"],step["scan"],step["flags"],step["down"],step["foreground"])
        physical=source_module.physical_key(step["vk"],step["scan"],step["flags"])
        if step["down"]:levels.add(physical)
        else:levels.discard(physical)
        if event is not None:
            if isinstance(step["created"],str):event["createdAt"]=float(step["created"])
            with patch("time.monotonic",return_value=100.):key_event(backend,event)
            step["expectedEvent"]={"vk":event["vk"],"physical":event["physicalVk"],"held":sorted(event["held"]),"tap":event["modifierTap"]}
        else:step["expectedEvent"]=None
        step["actions"],step["pickerKey"]=list(actions),picker[0] if picker else None

# Actual pure packet functions, without constructing RawMouseObserver (which binds OS APIs).
mouse_tree=ast.parse((QT/"client_native_mouse.py").read_text(encoding="utf-8"))
mouse_globals={}
for node in mouse_tree.body:
    if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=="SHORTCUT_BUTTON_FLAGS" for target in node.targets):
        exec(compile(ast.Module(body=[node],type_ignores=[]),"client_native_mouse.py","exec"),mouse_globals)
for name in ("mouse_phases","mouse_button_transitions"):
    production_function(QT/"client_native_mouse.py",None,name,mouse_globals)
mouse_cases=[]
for flags,raw_flags,x,y in ((0,0,3,4),(1,0,10,20),(3,0,0,0),(0x3fc,0,0,0),(0x400,0,0,0),(0,1,0,0),(2,0,1,1)):
    packet=SimpleNamespace(flags=raw_flags,lastX=x,lastY=y,buttonUnion=SimpleNamespace(buttons=SimpleNamespace(flags=flags)))
    mouse_cases.append(dict(buttonFlags=flags,flags=raw_flags,x=x,y=y,
        transitions=mouse_globals["mouse_button_transitions"](packet),phases=mouse_globals["mouse_phases"](packet)))
output=Path(__file__).with_name("python-oracle.json")
output.write_text(json.dumps(dict(source="Actual Python _transition_event / _key_event / mouse packet functions; no hook, OS key state or UI",cases=cases,mouse=mouse_cases),ensure_ascii=False,indent=2),encoding="utf-8")
print(f"Wrote {len(cases)} state sequences and {len(mouse_cases)} mouse packets to {output}")
