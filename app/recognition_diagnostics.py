"""Bounded measured recognition metadata. No decision, capture or state writer."""
import json
import math
import re


def s1_scan_failure(report, preferred_map_id=None):
    """Code-only failure summary, bounded before both log/privacy boundaries.

    Failed fits remain UNKNOWN. Counts cover the report; at most six example
    outcomes/reasons are retained, with an explicit omitted count and preferred
    candidate first. No pixels, matrices, paths or exception text are copied.
    """
    from collections import Counter
    from diagnostic_schema import CODES
    def code(value):
        return value if isinstance(value, str) and value in CODES else 'unknown-operation-failure'
    checked = report.get('checkedMapIds', [])
    supported = report.get('supportedMapIds', [])
    outcomes = report.get('outcomes', [])
    reasons = Counter(code(row.get('reason') or 's1-pose-not-verified')
        for row in outcomes if row.get('outcome') != 'supported')
    examples = sorted(outcomes, key=lambda row: row.get('mapId') != preferred_map_id)
    return dict(status=code(report.get('status')), scanComplete=report.get('scanComplete') is True,
        checkedCount=len(checked), supportedCount=len(supported),
        unscannedCount=len(report.get('unscannedMapIds', [])),
        elapsedMs=report.get('elapsedSeconds', 0) * 1000,
        preferred=dict(mapId=preferred_map_id),
        outcomes=[dict(mapId=row.get('mapId'), outcome=code(row.get('outcome')),
            reason=code(row.get('reason') or ('supported' if row.get('outcome') == 'supported'
                else 's1-pose-not-verified'))) for row in examples[:6]],
        omittedOutcomeCount=max(0, len(outcomes)-6),
        reasonCounts=[dict(reason=reason, count=count) for reason, count in reasons.most_common(6)],
        omittedReasonCount=max(0, len(reasons)-6))


def bounded_recognition(value, limit=2048):
    truncated = [False]
    def visit(item, depth=0):
        if depth > 7:
            truncated[0] = True
            return None
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float:
            return round(item, 5) if math.isfinite(item) else None
        if type(item) is str:
            # Stable product codes/IDs only, never arbitrary exception messages.
            return item if re.fullmatch(r'[A-Za-z0-9_.-]{0,96}', item) else None
        if type(item) is dict:
            result = {}
            for key, child in list(item.items())[:32]:
                if not isinstance(key, str) or re.search(r'path|title|username|nickname|account|executable', key, re.I):
                    continue
                result[key[:80]] = visit(child, depth + 1)
            truncated[0] |= len(item) > 32
            return result
        if type(item) in (list, tuple):
            truncated[0] |= len(item) > 6
            return [visit(child, depth + 1) for child in item[:6]]
        # Keep small numeric arrays only; no frame or mask conversion.
        if getattr(item, 'size', 10) <= 9 and hasattr(item, 'tolist'):
            return visit(item.tolist(), depth + 1)
        truncated[0] = True
        return None
    result = visit(value)
    if not isinstance(result, dict):
        result = {'value': result}
    def size():
        return len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    # Prune largest leaves first, keeping scalar stage/verdict/threshold evidence.
    while size() > limit - 40:
        choices = []
        def leaves(node):
            if isinstance(node, dict):
                for key, child in node.items():
                    if isinstance(child, dict):
                        leaves(child)
                    elif child is not None:
                        choices.append((len(json.dumps(child)), node, key))
            elif isinstance(node, list):
                for child in node:
                    if isinstance(child, dict): leaves(child)
        leaves(result)
        if not choices:
            result = {'stage': result.get('stage'), 'reason': result.get('reason')}
            break
        _, parent, key = max(choices, key=lambda row: row[0])
        parent[key] = None
        truncated[0] = True
    if truncated[0]:
        result['truncated'] = True
    return result


def control_band(frame, mode, observation, floor_read):
    """The current vertical detector ROI in original client pixels."""
    height, width = frame.shape[:2]
    boxes = []
    region = floor_read.get("controlSearchRegion")
    if (isinstance(region, (list, tuple)) and len(region) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in region)):
        x, y, w, h = region
        if 0 <= x < x+w <= 1 and 0 <= y < y+h <= 1:
            boxes.append([int(width*x), int(height*y),
                          min(width, math.ceil(width*(x+w))), min(height, math.ceil(height*(y+h)))])
    valid = [box for box in boxes if all(type(v) is int for v in box)
             and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height]
    if not valid:
        return None
    x0, y0 = min(b[0] for b in valid), min(b[1] for b in valid)
    x1, y1 = max(b[2] for b in valid), max(b[3] for b in valid)
    # Never widen an invalid ROI to the desktop/full frame.
    return [x0, y0, x1-x0, y1-y0]


def scale_observations(observation):
    """Avoid duplicating the selected hypothesis and thresholds in each scale."""
    fields = ('scale', 'controlScale', 'glyphs', 'spacing', 'dy', 'weakGlyphs',
              'controlBounds', 'highlightMeans', 'highlightDifference', 'mapOpen', 'floor')
    return [{key: row[key] for key in fields if key in row}
            for row in observation.get('diagnosticScales', [observation])]
