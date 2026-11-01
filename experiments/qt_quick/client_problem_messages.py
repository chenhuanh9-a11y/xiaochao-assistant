"""User-facing explanations retain the original failure separately for diagnosis."""

import re


_OPERATION_MESSAGES = {
    "capture-context-changed": ("游戏画面已变化", "窗口、画面或当前操作已变化，本次结果未采用。请保持游戏在前台，重新打开地图后再试。", "capture"),
    "capture-enable-rejected": ("游戏画面采集未启动", "当前无法开始采集游戏画面，本次识别未启动。请检查游戏连接状态；若仍失败，请查看技术详情。", "capture"),
    "capture-start-failed": ("游戏画面采集未启动", "本次未能开始采集游戏画面。请检查游戏连接状态，并在帮助页查看技术详情。", "capture"),
    "game-not-foreground": ("游戏不在前台", "当前游戏不在前台，本次游戏操作未执行。请切回游戏后重新操作。", "capture"),
    "foreground-denied": ("未能切回游戏", "系统没有完成切换到游戏窗口，本次操作未继续。请手动切回游戏后重试。", "capture"),
    "structure-work-obsolete": ("本次识别已过期", "画面或任务已变化，本次识别结果未采用。请等待当前操作完成后重新打开地图。", "recognition-alignment"),
    "alignment-work-cancelled": ("本次贴合已取消", "本次贴合没有继续执行。需要贴合时，请重新打开游戏地图。", "recognition-alignment"),
    "installed-resource-unavailable": ("当前地图资源不可用", "当前模式的地图资源无法读取，本次识别未启动。请到“地图资源”核对已安装资源，并查看技术详情。", "resource"),
    "mode-resources-unavailable": ("当前模式资源不可用", "当前模式所需地图资源尚不可用，本次操作未完成。请到“地图资源”核对该模式的资源。", "resource"),
    "resource-operation-busy": ("资源操作尚未完成", "另一个资源任务仍在进行，本次操作未开始。请等待该任务完成后重试。", "resource"),
    "resource-operation-requires-idle": ("暂不能修改地图资源", "本局或后台任务仍在运行，本次资源操作未开始。请先结束本局并等待后台任务完成。", "resource"),
    "entry-resource-unavailable": ("识别所需资源不可用", "当前地图的识别资源无法读取，本次未完成识别。请到“地图资源”核对资源，并查看技术详情。", "resource"),
    "registered-floor-resource-unavailable": ("当前楼层资源不可用", "当前楼层所需资源无法读取，本次未生成新的贴合。请到“地图资源”核对该地图楼层的资源。", "resource"),
}


def _reason_code(raw):
    # Only a stable leading token (optionally behind an exception type). A
    # filename or prose containing "capture" must not invent a focus failure.
    match = re.match(r"^(?:(?:[A-Za-z_]\w*(?:Error|Exception)|CaptureFallbackUnsupported):\s*)?([a-z][a-z0-9-]*)(?=$|[:;\s])", raw)
    return match.group(1) if match else ""


def input_message(status, raw, *, management=False, game=False):
    if status == "ready":
        return ("快捷键监听已开启；游戏操作需在已连接游戏的前台使用。" if game else
                "面板和本局快捷键已开启；连接游戏后可使用游戏操作快捷键。")
    if status == "starting":
        return "正在启动快捷键监听，请稍候。"
    if status == "suspended":
        return "游戏窗口暂时无法核验，游戏操作快捷键已暂停。请保持游戏窗口可见，等待重新连接。"
    if status == "waiting":
        return "尚未连接游戏，游戏操作快捷键暂不可用。请打开游戏，等待自动连接或手动连接。"
    if status == "disabled":
        return ("游戏操作快捷键已关闭，面板和本局快捷键仍可使用。" if management else
                "快捷键已关闭。需要使用时，请在连接游戏后启用。")
    if status == "conflict":
        if raw == "Shortcut action count does not match the product contract.":
            return "已保存的快捷键配置格式不完整，本次未启用。请在设置页重新核对并保存快捷键。"
        return "快捷键配置有冲突或无效，本次未启用。请在设置页检查重复按键并重新保存；具体冲突可在技术详情查看。"
    if status == "unknown":
        if raw == "Configure at least one shortcut before enabling.":
            return "尚未设置可用的快捷键。请在设置页录入并保存后，再启用快捷键。"
        if raw.startswith("Unknown shortcut actions:"):
            return "快捷键配置含有无法识别的操作，本次未启用。请在设置页核对配置，并查看技术详情。"
        if raw in {"The explicitly bound game window is unavailable.",
                   "Bound game window was destroyed; explicit rebind is required."}:
            return "游戏窗口尚不可用，游戏操作快捷键未启用。请打开游戏并重新连接；若仍失败，请查看技术详情。"
        return "当前快捷键状态无法确认，本次未启用。请在帮助页“最近问题”查看技术详情，再核对快捷键设置。"
    return "快捷键监听未能正常工作，本次快捷键操作不可用。请查看帮助页“最近问题”的技术详情，再检查连接和快捷键设置。"


def chest_message(state, raw):
    error = str(state.get("error") or "")
    if error.startswith("释放按键") or "释放重试已耗尽" in error:
        return ("释放按键未确认，开箱连点已停止。请先松开触发键，并检查游戏中该按键是否仍生效；"
                "详细原因可在帮助页“最近问题”查看。")
    if error.startswith("发送按键失败"):
        return "按键未能发送，开箱连点已停止。请先松开触发键，并在帮助页“最近问题”查看技术详情。"
    if error.startswith("开箱连点异常"):
        return "开箱连点发生异常，已停止发送按键。请松开触发键，并在帮助页“最近问题”查看技术详情。"
    if error:
        return "开箱连点发生异常，暂时无法确定原因。请松开触发键，并在帮助页“最近问题”查看技术详情。"
    # Validation/paused/ready messages from the core are already actionable
    # Chinese. Never hide their key conflict or imply a successful operation.
    return str(raw)


def operation_problem(receipt, state):
    raw = str(receipt.get('reason') or receipt.get('message') or 'unknown-operation-failure')[:8192]
    action = str(receipt.get('action', ''))
    phase = str(receipt.get('errorPhase') or 'recognition-alignment')
    kind = 'error' if receipt.get('error') else 'warning'
    title, summary = '操作未完成', '本次操作未完成，暂时无法确定原因。请在帮助页“最近问题”展开技术详情，并将记录提供给支持人员。'
    code = _reason_code(raw)
    if code == 'capture-safe-fallback-unsupported':
        title, phase, kind = '当前系统不支持安全画面回退', 'capture', 'error'
        summary = ('本次游戏客户区采集失败，当前系统不支持安全的屏幕采集回退；'
                   '该回退需要 Windows 10 2004（内部版本 19041）或更高版本。'
                   '普通客户区采集仍可使用，请保持游戏窗口可见并重新打开地图重试。'
                   '若持续出现，需在支持此回退能力的系统上使用；具体能力与原始触发原因已保存在帮助页。')
    elif code == 'live-runtime-requires-full-client-capture':
        title, phase, kind = '采集画面尺寸不一致', 'capture', 'error'
        summary = '采集画面未覆盖当前游戏客户区，本次识别已停止。请重新打开地图；详细尺寸可在帮助页的“最近问题”查看。'
    elif code in _OPERATION_MESSAGES:
        title, summary, phase = _OPERATION_MESSAGES[code]
    elif code == 'difficult-f2-entry-recognition-disabled':
        title, phase = '困难二楼暂不支持入局识别', 'identity'
        summary = '请先在一楼完成地图识别；已识别的地图仍可正常切换到二楼并继续贴合。'
    elif code == 'identity-not-uniquely-verified':
        title, summary = '地图仍需确认', '当前画面还不能唯一确认地图，请完整打开地图后重试，或手动选择地图。'
        phase = 'identity'
    elif code == 'candidate-awaiting-confirmation':
        title, summary = '请确认地图', '已保留所选地图，请打开游戏地图并确认候选后继续。'
        phase = 'identity'
    elif code == 'current-frame-floor-conflict':
        def floor_label(value):
            value = str(value)
            return 'B1' if value in {'B1', '-1'} else 'F' + value if value in {'1', '2'} else '待确认楼层'
        observed = floor_label(receipt.get('observedFloor', state.get('observedFloor')))
        target = floor_label(receipt.get('targetFloor', state.get('floor')))
        title, phase = '目标楼层与当前画面不一致', 'floor'
        summary = f'当前画面仍是 {observed}，目标为 {target}，本次未进行贴合。请等待游戏楼层高亮稳定后重试；技术详情可在“最近问题”查看。'
    elif code in {'structure-current-geometry-failed', 'initial-structure-supported-chains-insufficient'}:
        title, phase = '本次地图位置未核对通过', 'recognition-alignment'
        summary = ('当前画面的结构或位置证据不足，本次未生成新的贴合。'
                   '请完整打开游戏地图并等待画面稳定后重试；已有有效贴合是否保留，以当前显示状态为准。'
                   '技术原因可在“最近问题”查看。')
    elif code in {'current-floor-unavailable', 'floor-policy-deferred', 'candidate-or-floor-missing'}:
        title, summary = '暂未读到楼层', '请完整打开游戏地图，等待楼层高亮稳定后再试。'
        phase = 'floor'
    problem = dict(title=title, summary=summary, raw=raw, action=action, phase=phase, kind=kind,
                mode=state.get('mode', ''), mapId=state.get('mapId', ''), floor=state.get('floor', ''),
                captureDiagnostics=receipt.get('captureDiagnostics') or {},
                traceback=str(receipt.get('traceback') or '')[:8192] if receipt.get('error') else '')
    if receipt.get('error'):
        for key in ('exceptionType', 'exceptionOrigin', 'exceptionAction'):
            if isinstance(receipt.get(key), str):
                problem[key] = receipt[key][:128]
    return problem
