"""Native difficult resources feed the existing identity rules, without author RGB."""
from pathlib import Path
import json

import cv2
import numpy as np

from map_identity_engine import MapIdentityEngine
from nightmare_candidate import NightmareMap, NightmarePack


def _validate_imported_identity_ids(raw):
    """Reject path-like IDs before an imported pack reaches personal storage.

    This checks the DTO's IDs, inventory size and canonical local paths. The
    base decoder must still validate declared files and typed layer semantics.
    Trusted legacy callers retain their original parsing below.
    """
    from star_map_import import _relative

    def segment(value):
        try:
            relative = _relative(value)
        except (TypeError, ValueError) as error:
            raise ValueError('native identity ID is not a safe path segment') from error
        if relative != value or '/' in value:
            raise ValueError('native identity ID is not a safe path segment')
        return value

    def unique(values):
        checked = [segment(value) for value in values]
        if len({value.casefold() for value in checked}) != len(checked):
            raise ValueError('native identity IDs collide on Windows')
        return checked

    if not isinstance(raw, dict):
        raise ValueError('native identity manifest must be an object')
    segment(raw.get('packId'))
    floors, rows = raw.get('floors'), raw.get('maps')
    if (not isinstance(floors, list) or not 1 <= len(floors) <= 16
            or not isinstance(rows, list) or not 1 <= len(rows) <= 256):
        raise ValueError('native identity inventory exceeds decoder limits')
    floor_ids = set(unique(floors))
    map_ids = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('native identity map must be an object')
        map_ids.append(segment(row.get('id')))
        segment(row.get('routeProfileId'))
        layers = row.get('layers')
        if not isinstance(layers, dict) or not layers or not set(layers) <= floor_ids:
            raise ValueError('native identity layers must use declared floor IDs')
        for path in [row.get('oneFlow'), *layers.values()]:
            if path is not None:
                try:
                    if _relative(path) != path:
                        raise ValueError('noncanonical resource path')
                except (TypeError, ValueError) as error:
                    raise ValueError('native identity resource path is invalid') from error
    unique(map_ids)


class NativeDifficultPack:
    mode = 'difficult'
    filter = NightmarePack.filter
    layer_role = 'structure-reference'

    def __init__(self, root, manifest_relative, *, display_context=None, expected_mode=None,
                 expected_season=None, read_resource=None):
        self.mode = expected_mode or type(self).mode
        self.resource_root = self.source_root = Path(root).resolve()
        self.display_context = display_context
        self.manifest_path = self.local(manifest_relative)
        manifest_text = (self.manifest_path.read_text(encoding='utf-8') if read_resource is None
                         else read_resource(self.manifest_path).decode('utf-8'))
        self.raw = json.loads(manifest_text)
        if read_resource is not None:
            _validate_imported_identity_ids(self.raw)
        if self.raw.get('schemaVersion') != 1 or self.raw.get('mode') != self.mode:
            raise ValueError('native difficult identity pack is incompatible')
        if expected_season is not None and self.raw.get('season') != expected_season:
            raise ValueError('native identity season mismatch')
        self.pack_id = self.raw['packId']
        self.display_name = self.raw.get('displayName', '困难 · 原生结构')
        self.floors = tuple(map(str, self.raw['floors']))
        self.default_entry_floor = int(self.raw.get('defaultEntryFloor', 1))
        self.identity_profiles = {}
        self.route_profiles = {}
        self.maps = []
        for row in self.raw['maps']:
            if row['identityProfileId'] not in {'shared-structure', 'zhan10-speed'}:
                raise ValueError('native difficult identity must use typed shared geometry')
            self.identity_profiles[row['id']] = row['identityProfileId']
            self.route_profiles[row['id']] = row['routeProfileId']
            self.maps.append(NightmareMap(id=row['id'], name=row['name'], direction=row['direction'],
                door_count=int(row.get('doorCount', 0)), shape=row['shape'],
                side_door_crop=tuple(row.get('sideDoorCrop', row.get('previewBox', (0,0,0,0)))),
                one_flow=self.local(row['oneFlow']) if row.get('oneFlow') is not None else None,
                layers={str(floor):self.local(path) if path is not None else None
                        for floor,path in row['layers'].items()}))
        self.by_id = {row.id:row for row in self.maps}
        if not self.maps or len(self.by_id) != len(self.maps):
            raise ValueError('native difficult identity inventory is empty or duplicated')

    def local(self, relative):
        path = (self.resource_root/str(relative)).resolve()
        if not path.is_relative_to(self.resource_root):
            raise ValueError('native identity resource escapes root')
        return path

    def validate(self):
        missing = [f'{item.id}/{floor}: declared-unavailable' if path is None else str(path)
                   for item in self.maps for floor,path in item.layers.items()
                   if path is None or not path.is_file()]
        return dict(success=not missing, maps=len(self.maps), layers=sum(len(m.layers) for m in self.maps),
            sourceRoot=str(self.source_root), missing=missing, errors=[],
            scope='typed native structure inventory; optional author display excluded')


class NativeDifficultCandidateEngine(MapIdentityEngine):
    """Original ranking/validators, with the complete declared competitor scope."""
    def rank(self, frame, floor='1', limit=30, *, budget_seconds=None):
        if not getattr(self, 'qt_rank_ready', True):
            raise ValueError(';'.join(getattr(self, 'qt_rank_gaps', ())) or 'recognition-rank-unavailable')
        structure_rank = getattr(self, 'qt_s1_structure_rank', None)
        if getattr(self, 'qt_season', None) == 's1' and callable(structure_rank):
            # Caller supplies the floor. No icon/entrance or season inference.
            return structure_rank(frame, floor=str(floor), budget_seconds=budget_seconds)
        return super().rank(frame, floor=floor, limit=limit, budget_seconds=budget_seconds)

    def prepare_entry_observation(self, frame, floor, *, deadline, prior=None):
        if not getattr(self, 'qt_rank_ready', True) and not getattr(self, 'qt_structure_scan_ready', False):
            raise ValueError(';'.join(getattr(self, 'qt_rank_gaps', ())) or 'recognition-rank-unavailable')
        if getattr(self, 'qt_season', None) == 's1':
            raise ValueError('proposal-only-entry-evidence')
        return super().prepare_entry_observation(frame, floor, deadline=deadline, prior=prior)

    def collect_observation_rows(self, work):
        return self._apply_native_inventory(super().collect_observation_rows(work), work.floor)

    def _apply_native_inventory(self, rows, floor):
        # A missing native competitor cannot disappear from a uniqueness
        # decision. Preserve scores/order and expose the successful partial
        # fits as proposals, just as the existing shared entry inventory does.
        missing = sorted(item.id for item in self.pack.maps
                         if str(floor) in item.layers and item.layers[str(floor)] is None)
        self.last_native_inventory = dict(complete=not missing, missingMapIds=missing,
            expectedMapIds=[item.id for item in self.pack.maps if str(floor) in item.layers])
        if missing:
            for row in rows:
                row.update(nativeInventory=self.last_native_inventory,
                    nativeInventoryIdentitySuppressed=bool(row.get('identityHardGatePassed')),
                    identityHardGatePassed=False, strongSuggestion=False,
                    reason='native-identity-inventory-incomplete')
                entry = row.get('difficultEntry')
                if isinstance(entry, dict):
                    self._apply_shared_entry_inventory([entry], {}, dict(
                        entryInventoryComplete=False, entryInventoryMissingMapIds=missing))
        return rows

    def route_image(self, map_id, floor):
        reference = self._shared_structure_product(map_id, floor)
        if reference is None:
            return None
        key = (str(map_id), str(floor))
        with self._route_lock:
            image = self._route_image_cache.get(key)
            if image is None:
                image = cv2.cvtColor(reference.clean_mask * np.uint8(255), cv2.COLOR_GRAY2BGR)
                image.setflags(write=False)
                self._route_image_cache[key] = image
                while len(self._route_image_cache) > 6:
                    self._route_image_cache.pop(next(iter(self._route_image_cache)))
            return image
