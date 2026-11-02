"""Bounded measured recognition metadata. No decision, capture or state writer."""
import json
import math
import re


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
    """Union of the actual top-right detector ROIs, in original client pixels."""
    height, width = frame.shape[:2]
    boxes = []
    if mode == 'difficult':
        for row in observation.get('diagnosticScales', [observation]):
            box = row.get('controlBounds')
            if isinstance(box, (list, tuple)) and len(box) == 4:
                boxes.append(box)
    else:
        from map_interface_observation import MAP_REGIONS
        from client_space_geometry import CLIENT_COMPONENT_POLICIES, normalized_rect_to_client
        for name in ('floorSwitch', 'closeButton'):
            boxes.append(normalized_rect_to_client((width, height), MAP_REGIONS[name][:4],
                policy=CLIENT_COMPONENT_POLICIES['ui-roi']))
        from map_interface_observation import floor_control_vertical_geometry
        vertical = floor_control_vertical_geometry(width, height)
        # Localization searches this wider strip before measuring the triplet.
        y0 = int(height * vertical['searchTop'])
        y1 = min(height, int(height * (vertical['searchBottom'] + vertical['regionHeight'])))
        boxes.append([int(width * .70), y0, int(width * .995), y1])
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
