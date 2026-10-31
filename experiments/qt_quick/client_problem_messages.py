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
    elif raw == 'difficult-f2-entry-recognition-disabled':
        title, phase = '困难二楼暂不支持入局识别', 'identity'
        summary = '请先在一楼完成地图识别；已识别的地图仍可正常切换到二楼并继续贴合。'
    elif raw == 'identity-not-uniquely-verified':
        title, summary = '地图仍需确认', '当前画面还不能唯一确认地图，请完整打开地图后重试，或手动选择地图。'
        phase = 'identity'
    elif raw == 'candidate-awaiting-confirmation':
        title, summary = '请确认地图', '已保留所选地图，请打开游戏地图并确认候选后继续。'
        phase = 'identity'
    elif raw == 'current-frame-floor-conflict':
        def floor_label(value):
            value = str(value)
            return 'B1' if value in {'B1', '-1'} else 'F' + value if value in {'1', '2'} else '待确认楼层'
        observed = floor_label(receipt.get('observedFloor', state.get('observedFloor')))
        target = floor_label(receipt.get('targetFloor', state.get('floor')))
        title, phase = '目标楼层与当前画面不一致', 'floor'
        summary = f'当前画面仍是 {observed}，目标为 {target}，本次未进行贴合。请等待游戏楼层高亮稳定后重试；技术详情可在“最近问题”查看。'
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
