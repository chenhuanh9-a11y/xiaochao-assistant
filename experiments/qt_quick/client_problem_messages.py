"""User-facing explanations retain the original failure separately for diagnosis."""

from diagnostic_schema import CODES
from client_fit_reporting import RETRY_REASONS


def problem_reason(value):
    """Persist only an exact registered product code, never exception text."""
    return value if isinstance(value, str) and value in CODES else 'unknown-operation-failure'


def operation_problem(receipt, state):
    raw = str(receipt.get('reason') or receipt.get('message') or 'unknown-operation-failure')[:8192]
    action = str(receipt.get('action', ''))
    phase = str(receipt.get('errorPhase') or 'recognition-alignment')
    kind = 'error' if receipt.get('error') else 'warning'
    title, summary = '操作未完成', '本次操作未完成，原因已保存在帮助页的“最近问题”。'
    preparation_observation = receipt.get('preparationObservation') or {}
    width, height = preparation_observation.get('frameWidth'), preparation_observation.get('frameHeight')
    if (raw in {'map-open-preparation-deadline', 'current-floor-unavailable'}
            and isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        size = f'当前游戏客户区为 {width} × {height} 像素。'
        shape = preparation_observation.get('shapeReason')
        if shape == 'capture-shape-width' or preparation_observation.get('floorControlSizeEligible') is False:
            title, phase = '游戏窗口尺寸过小', 'map-preparation'
            summary = size + '当前尺寸未满足地图／楼层控件检查所需的宽至少 1000、高至少 600 像素。请适当增大游戏窗口，再完整打开地图重试。'
        elif shape == 'capture-shape-aspect':
            title, phase = '游戏窗口比例过窄', 'map-preparation'
            summary = size + '当前宽高比低于 1.25。请适当加宽游戏窗口（宽度至少为高度的 1.25 倍），再打开地图重试。'
        elif preparation_observation.get('mapDetected') and preparation_observation.get('floorLayout') == 'unlocalized':
            title, phase = '尚未定位楼层控件', 'floor'
            summary = size + '已看到地图，但尚未定位楼层按钮。请确保楼层按钮未被遮挡，等待地图展开稳定后重试。'
        elif raw == 'current-floor-unavailable':
            title, phase = '暂未读到楼层', 'floor'
            summary = size + '请完整打开游戏地图，等待楼层高亮稳定后再试。'
        else:
            title, phase = '地图画面尚未确认', 'map-preparation'
            summary = size + '当前地图画面证据不足，不能据此判断窗口过小。请完整打开地图、避免遮挡并等待画面稳定后重试。'
    elif raw in RETRY_REASONS:
        title, summary = '地图位置仍在核对', RETRY_REASONS[raw] + '，请保持地图打开并等待画面稳定。'
    elif raw == 'initial-structure-query-unavailable':
        title, summary = '当前结构输入不可用', '当前画面未提供可用的结构输入，请重新打开地图；详情已保存在最近问题。'
    elif raw == 'initial-structure-identity-not-current':
        title, summary = '地图身份已变化', '本次贴合所用地图身份已失效，请等待当前地图重新核对。'
    elif raw == 'initial-structure-resource-unavailable':
        title, summary = '贴合结构资源不可用', '请检查当前图包的结构资源；详情已保存在最近问题。'
    elif raw == 'initial-structure-coordinate-mismatch':
        title, summary = '结构坐标不一致', '当前结构资源坐标未核对通过，请检查图包。'
    elif raw == 'initial-structure-author-unverified':
        title, summary = '显示图位置未核对通过', '显示图与结构的对应关系未验证，请检查当前路线资源。'
    elif 'live-runtime-requires-full-client-capture' in raw:
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
    problem = dict(title=title, summary=summary, raw=raw, reason=problem_reason(receipt.get('reason')), action=action, phase=phase, kind=kind,
                mode=state.get('mode', ''), mapId=state.get('mapId', ''), floor=state.get('floor', ''),
                captureDiagnostics=receipt.get('captureDiagnostics') or {},
                preparationObservation=preparation_observation,
                traceback=str(receipt.get('traceback') or '')[:8192])
    if receipt.get('error'):
        for key in ('exceptionType', 'exceptionOrigin', 'exceptionAction'):
            if isinstance(receipt.get(key), str):
                problem[key] = receipt[key][:128]
    return problem
