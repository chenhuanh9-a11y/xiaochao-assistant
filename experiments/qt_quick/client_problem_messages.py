"""User-facing explanations retain the original failure separately for diagnosis."""


def operation_problem(receipt, state):
    raw = str(receipt.get('reason') or receipt.get('message') or 'unknown-operation-failure')[:8192]
    action = str(receipt.get('action', ''))
    phase = str(receipt.get('errorPhase') or 'recognition-alignment')
    kind = 'error' if receipt.get('error') else 'warning'
    title, summary = '操作未完成', '本次操作未完成，原因已保存在帮助页的“最近问题”。'
    if 'live-runtime-requires-full-client-capture' in raw:
        title, phase, kind = '采集画面尺寸不一致', 'capture', 'error'
        summary = '采集画面未覆盖当前游戏客户区，本次识别已停止。请重新打开地图；详细尺寸可在帮助页的“最近问题”查看。'
    elif 'capture' in raw.casefold() or 'foreground' in raw.casefold():
        title, phase = '游戏画面暂不可用', 'capture'
        summary = '未能取得有效的游戏画面。请保持游戏窗口可见、尺寸稳定，并在游戏中重新打开地图。详情已保存在帮助页。'
    elif raw == 'identity-not-uniquely-verified':
        title, summary = '地图仍需确认', '当前画面还不能唯一确认地图，请完整打开地图后重试，或手动选择地图。'
        phase = 'identity'
    elif raw == 'candidate-awaiting-confirmation':
        title, summary = '请确认地图', '已保留所选地图，请打开游戏地图并确认候选后继续。'
        phase = 'identity'
    elif raw in {'structure-current-geometry-failed', 'initial-structure-supported-chains-insufficient'}:
        title, phase = '本次地图位置未核对通过', 'recognition-alignment'
        summary = ('当前画面的结构或位置证据不足，本次未生成新的贴合。'
                   '请完整打开游戏地图并等待画面稳定后重试；已有有效贴合是否保留，以当前显示状态为准。'
                   '技术原因可在“最近问题”查看。')
    elif raw in {'current-floor-unavailable', 'floor-policy-deferred', 'candidate-or-floor-missing'}:
        title, summary = '暂未读到楼层', '请完整打开游戏地图，等待楼层高亮稳定后再试。'
        phase = 'floor'
    return dict(title=title, summary=summary, raw=raw, action=action, phase=phase, kind=kind,
                mode=state.get('mode', ''), mapId=state.get('mapId', ''), floor=state.get('floor', ''),
                captureDiagnostics=receipt.get('captureDiagnostics') or {},
                traceback=str(receipt.get('traceback') or '')[:8192])
