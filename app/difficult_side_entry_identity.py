"""Stateless local structure candidates; never grants identity or UI pose.

rank(frame, floor=None, budget_seconds=...) returns (rows, evidence).
The caller owns mode selection, identity competition and all displayed state.
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np


class MapLocalEntryIdentity:
    def __init__(self, root, *, route_loader=None, shared_reference_provider=None, geometry_provider=None, author_visible_provider=None,
                 mode='difficult', profile_id='zhan10-speed', floors=('1','2'),
                 exit_icon_path=None, structure_store=None, profile_for_map=None,
                 read_resource=None, typed_index_path=None, semantic_side_entries=True,
                 passport_source_available=True):
        if type(semantic_side_entries) is not bool:
            raise TypeError('semantic_side_entries-must-be-bool')
        if type(passport_source_available) is not bool:
            raise TypeError('passport_source_available-must-be-bool')
        self.root = Path(root)
        self.mode = mode
        self.profile_id = profile_id
        self.profile_for_map = profile_for_map or (lambda map_id: self.profile_id)
        self.floors = tuple(floors)
        self.semantic_side_entries = semantic_side_entries
        self.passport_source_available = passport_source_available
        self.exit_icon_path = (Path(exit_icon_path) if exit_icon_path is not None else
                               self.root.parent / 'difficult-exit-icon.json') if semantic_side_entries else None
        self.route_loader = route_loader
        self.read_resource = read_resource
        self.shared_reference_provider = shared_reference_provider
        self.geometry_provider = geometry_provider
        self.author_visible_provider = author_visible_provider
        if structure_store is None:
            if read_resource is not None:
                raise ValueError('entry-reader-requires-structure-store')
            from author_structure_resources import AuthorStructureResourceStore
            structure_store = AuthorStructureResourceStore(self.root / 'index.json', self.root)
        self.structures = structure_store
        path = self.root / 'side-entries.json'
        payload = json.loads(path.read_text(encoding='utf8') if read_resource is None else read_resource(path))
        if payload.get('schemaVersion') != 1 or payload.get('mode') != self.mode or payload.get('profileId') != self.profile_id:
            raise ValueError('entry index mode/profile mismatch')
        self.semantic_gaps = payload['semanticGaps']
        self.entries = []
        if read_resource is not None:
            from side_entry_geometry import _bounded_list, _bounded_points
            _bounded_list(payload['entries'], 256, 'side entries')
            total_turns = 0
            for item in payload['entries']:
                turns = _bounded_list(item['turns'], 1024, 'side turns')
                total_turns += len(turns)
                if total_turns > 65536:
                    raise ValueError('entry-resource-complexity: side turns total')
                for turn in turns:
                    _bounded_points([turn['point']], 1, 'side turn point')
                    if len(_bounded_points(turn['rays'], 2, 'side turn rays')) != 2 or type(turn['next']) is not int:
                        raise ValueError('invalid side turn shape')
                for gate in _bounded_list(item.get('gates', []), 64, 'side gates'):
                    _bounded_points([gate['center']], 1, 'side gate center')
        for item in payload['entries']:
            points = np.asarray([t['point'] for t in item['turns']], float).reshape(-1, 2)
            rays = np.asarray([t['rays'] for t in item['turns']], float).reshape(-1, 2, 2)
            if not np.isfinite(points).all() or not np.isfinite(rays).all() or item['unit'] <= 0:
                raise ValueError('invalid side graph')
            pairs = np.array([(i, t['next']) for i, t in enumerate(item['turns']) if 0 <= t['next'] < len(points)], int).reshape(-1, 2)
            self.entries.append((item, points, rays, pairs))
        self.registered_floors=frozenset((str(item['mapId']),str(item['floor']))
            for item,_,_,_ in self.entries)
        if semantic_side_entries:
            from side_entry_geometry import load_side_doors
            self.side_doors, self.side_door_error = load_side_doors(self.root, self.entries, self.mode, self.profile_id,
                read_resource=read_resource, typed_index_path=typed_index_path)
            from side_entry_search import load_index
            self.side_search, self.side_search_error = load_index(self.root, self.entries, self.side_doors,
                read_resource=read_resource)
        else:
            self.side_doors, self.side_search = {}, {}
            self.side_door_error = self.side_search_error = None
        self.passports = []
        self.entry_coverage = []
        self.passport_error = None
        if not passport_source_available:
            self.passport_error = 'entry-passports-source-unavailable'
        else:
            try:
                path = self.root / 'entry-passports.json'
                passport = json.loads(path.read_text(encoding='utf8') if read_resource is None else read_resource(path))
                if (passport.get('schemaVersion'), passport.get('mode'), passport.get('profileId')) != (1, self.mode, self.profile_id):
                    raise ValueError('entrance passport mode/profile mismatch')
                if read_resource is not None:
                    self._bound_passports(passport)
                self.passports = passport['entries']
                self.entry_coverage = passport['coverage']
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.passport_error = str(error)
        self._exit_recognizer = None

    @staticmethod
    def _bound_passports(passport):
        from side_entry_geometry import _bounded_list, _bounded_points, _bounded_wall_lines
        _bounded_list(passport['coverage'], 256, 'passport coverage')
        for item in _bounded_list(passport['entries'], 1024, 'passports'):
            _bounded_points([item['center']], 1, 'passport center')
            _bounded_wall_lines(item['knownWallLines'], 256, 'passport walls')
            for room in _bounded_list(item['localInstances'], 128, 'passport instances') + _bounded_list(item.get('optionalRooms', []), 64, 'optional rooms'):
                _bounded_points(room['outline'], 256, 'passport outline')
                _bounded_wall_lines(room.get('knownWallLines', []), 256, 'instance walls')
                for port in _bounded_list(room.get('ports', []), 64, 'instance ports'):
                    _bounded_points(port['points'], 64, 'port points')

    def has_registered_floor(self,map_id,floor):
        """Whether the shared local-structure locator has data for this floor."""
        return (str(map_id),str(floor)) in self.registered_floors

    def _entry_observations(self, frame, *, context=None):
        """Reuse actual exit/rectangle primitives without altering nightmare."""
        if not self.semantic_side_entries:
            return []
        from side_door_recognition import SideDoorRecognizer
        from nightmare_candidate import visible_map_component, local_side_door_rectangle, live_side_door_direction
        if self._exit_recognizer is None:
            self._exit_recognizer = SideDoorRecognizer(self.exit_icon_path, read_resource=getattr(self, 'read_resource', None))
        bounds = context.get('observationBounds') if context is not None else None
        if bounds is None:
            visible = visible_map_component(frame)
            if visible is None:
                return []
            _, bounds, _ = visible
        # The current observation already located the map. Use the live UI
        # probe and full-client coordinates; a missed icon keeps structure search.
        search = None
        if context is not None:
            x0, y0, x1, y1 = bounds
            padding = 2 * max(self._exit_recognizer.exit_icon.shape[:2])
            search = (x0-padding, y0-padding, x1+padding, y1+padding)
        anchors = self._exit_recognizer.find_anchors(frame, allow_unusual_scales=context is None,
                                                    match_workers=2, search_bounds=search)
        observations = []
        for anchor in anchors:
            icon = dict(center=[anchor['x']+anchor['width']/2, anchor['y']+anchor['height']/2],
                        size=[anchor['width'], anchor['height']], score=anchor['score'])
            # The reused direction primitive cannot accept an icon below .58,
            # even with a perfect local rectangle. Preserve its observation but
            # do not measure a rectangle which cannot affect reliable exits.
            if anchor['score'] < .58:
                observations.append(dict(icon=icon,rectangle=None,reliable=False,
                                         direction=dict(reliable=False,reason='icon-below-local-reliability-threshold')))
                continue
            # Do not subtract a second real exit's score and erase both exits.
            rectangle = local_side_door_rectangle(frame, bounds, icon, _reuse_floor_color_mask=True) if bounds else None
            direction = live_side_door_direction(bounds, icon, rectangle) if bounds else dict(reliable=False)
            reliable = bool(anchor['score'] >= .78 or direction.get('reliable'))
            observations.append(dict(icon=icon, rectangle=rectangle, direction=direction, reliable=reliable))
        return observations

    @staticmethod
    def _entry_wall_arrays(qlines, slines):
        qa = np.array([[g[k] for k in ('axis', 'sign', 'normal', 'lo', 'hi')] for g in qlines]).reshape(-1, 5)
        sa = np.array([[g[k] for k in ('axis', 'sign', 'normal', 'lo', 'hi')] for g in slines]).reshape(-1, 5)
        oriented = np.all(qa[:, None, :2] == sa[None, :, :2], axis=2)
        qi, si = np.nonzero(oriented)
        starts = np.r_[0, np.flatnonzero(np.diff(qi))+1] if len(qi) else np.array([], dtype=int)
        return qa, sa, oriented, (qi, si, starts, qi[starts], qa[qi], sa[si])

    def _fit_entry_local_walls(self, qlines, slines, arrays, scale, translation, fit_cache):
        from single_map_structure import initial_wall_geometry
        qa, sa, oriented, _ = arrays
        normal = np.abs(scale*sa[None,:,2]+translation[qa[:,0].astype(int),None]-qa[:,None,2])
        tangent = translation[1-qa[:,0].astype(int),None]
        overlap = np.minimum(qa[:,None,4],scale*sa[None,:,4]+tangent)-np.maximum(qa[:,None,3],scale*sa[None,:,3]+tangent)
        supported = np.any(oriented & (normal<=3) & (overlap>=np.minimum(12,qa[:,None,4]-qa[:,None,3]-1)),axis=1)
        local_lines = [q for q,keep in zip(qlines,supported) if keep]
        subset_key = tuple(np.flatnonzero(supported))
        if subset_key not in fit_cache:
            # Existing per-item/per-frame fit cache already keys this exact
            # ordered query subset. Its source lines cannot change in a rank.
            # Reuse immutable correspondence arrays, not a solved pose/result.
            fit_cache[subset_key] = (self._entry_wall_arrays(local_lines,slines), {})
        local_arrays, subset_cache = fit_cache[subset_key]
        return initial_wall_geometry(local_lines,slines,scale,translation,
                                     local_arrays,subset_cache)

    @staticmethod
    def _entry_room_evidence(item, matrix, qlines, query):
        """Room walls AND a registered connecting doorway must be observed.

        Fogged rooms do not refute a candidate; they leave its passport unresolved.
        No colour/room label supplied by the caller participates in matching.
        """
        scale = matrix[0, 0]
        shift = matrix[:2, 2]
        witnesses = []
        for room in item['localInstances']:
            if room['kind'] != 'room' or not room['connections']:
                continue
            planes = []
            for source in room['knownWallLines']:
                axis = source['axis']
                normal = scale*source['normal']+shift[axis]
                lo, hi = scale*np.array([source['lo'], source['hi']])+shift[1-axis]
                for line in qlines:
                    overlap = min(hi, line['hi'])-max(lo, line['lo'])
                    if line['axis'] == axis and line['sign'] == source['sign'] and abs(normal-line['normal']) <= 3 and overlap >= 12:
                        key = (axis, line['sign'], line['normal'])
                        if key not in planes:
                            planes.append(key)
            # The final registered path edge owns a room port; use its actual
            # relative coordinates, not nearest-room proximity or a room count.
            measured_ports = []
            for port in room['ports']:
                if port['name'] != room['connectingPort']:
                    continue
                center = np.mean(port['points'], axis=0)*item['unit']+item['center']
                point = np.rint(scale*center+shift).astype(int)
                x, y = point
                if 1 <= x < query.shape[1]-1 and 1 <= y < query.shape[0]-1 and query[y-1:y+2, x-1:x+2].mean() >= .5:
                    measured_ports.append(port['name'])
            if len(planes) >= 2 and measured_ports:
                witnesses.append(dict(instance=room['instance'], material=room['material'], roomTypeId=room['roomTypeId'],
                                      rotation=room['rotation'], supportedPlanes=planes, visiblePorts=measured_ports,
                                      connectionPath=room['connections']))
        return dict(verified=bool(witnesses), rooms=witnesses)

    @staticmethod
    def _refined_entry_fits(fits):
        """Deduplicate repeated references to the exact cached fit only.

        Normal-plane containment does not prove tangent-segment identity or
        whole-frame validity. Never eliminate a partial fit before verification.
        """
        result, seen = [], set()
        for fit in fits:
            if id(fit) not in seen:
                seen.add(id(fit))
                result.append(fit)
        return result

    @staticmethod
    def _verified_entry_pose_groups(rows, bounds):
        """Group only already verified poses; retain failures and full evidence.

        Every pair in a group must be equivalent in both inverse-mapped source
        domains. This prevents a chain of near poses spanning more than 3px.
        """
        x0, y0, x1, y1 = bounds
        client = np.array([[x0,y0,1],[x1,y0,1],[x0,y1,1],[x1,y1,1]])
        retained, groups = [], []
        def same_pose(a, b):
            if any(a[k] != b[k] for k in ('mapId','floor','sourceEntryId','entryKind')):
                return False
            left, right = np.asarray(a['matrixSourceToClient']), np.asarray(b['matrixSourceToClient'])
            return all(np.linalg.norm(((client@np.linalg.inv(domain).T)@(left-right).T)[:,:2],axis=1).max() <= 3
                       for domain in (left,right))
        for row in rows:
            if not row['verification'].get('success') or not row['entryEvidence']['localGeometryVerified']:
                retained.append(row)
                continue
            group = next((g for g in groups if all(same_pose(row, other) for other in g)), None)
            if group is None:
                groups.append([row])
                retained.append(row)
            else:
                group.append(row)
        return retained, [g for g in groups if len(g)>1]

    @staticmethod
    def _verify_entry_refinement_chain(chain, converged, build_row, deadline):
        """Authorize a refined result, or recover every earlier proposal.

        Intermediate fits are not authorized poses. When the stable result is
        invalid, none of its predecessors may be discarded without verification.
        """
        evaluated, complete = [], True
        for index in range(len(chain)-1,-1,-1):
            if time.perf_counter() >= deadline:
                complete = False
                break
            row = build_row(chain[index])
            evaluated.append(row)
            if (index == len(chain)-1 and converged and row['verification']['success']
                    and row['entryEvidence']['localGeometryVerified']):
                break
        evidence = []
        for fit in chain:
            verified = next((r['verification'] for r in evaluated if np.array_equal(r['matrixSourceToClient'],fit['matrix'])),None)
            evidence.append(dict(matrixSourceToClient=fit['matrix'].tolist(),planes=fit['planes'],
                                 heldOutNormals=fit['heldOutNormals'],verification=verified,
                                 status='current-frame-verified' if verified is not None else 'intermediate-fit-not-authorized'))
        for row in evaluated:
            row['entryEvidence']['localRefinementHistory'] = evidence
            row['entryEvidence']['localRefinementConverged'] = converged
        return evaluated, complete

    @staticmethod
    def _entry_correspondence_extends(previous, proposed):
        # IDs refer to immutable source/query contour segments (including
        # tangent ranges), not just coincident normal coordinates.
        keys=('axis','sign','queryEdge','sourceEdge')
        return all(g.get('queryEdge') is not None and g.get('sourceEdge') is not None
                   and any(all(g.get(k)==h.get(k) for k in keys) for h in proposed['planes'])
                   for g in previous['planes'])

    def check_entry_evidence(self, frame, row, observation=None):
        """Check a fallback pose against applicable current exits; no search.

        An unknown source inventory/direction is not a contrary observation.
        The caller may pass THIS frame's rank_entry diagnostics to reuse pixels.
        """
        if self._side_door_contradicted(row):
            return dict(applicable=True, contradicted=True, checks=[],
                        reason='current-side-door-geometry', coverageComplete=True)
        items = [p for p in self.passports if p['mapId'] == row['mapId'] and p['floor'] == str(row['floor'])]
        coverage = [c for c in self.entry_coverage if c['mapId'] == row['mapId'] and c['floor'] == str(row['floor'])]
        inventory_complete = bool(coverage and all(c['complete'] for c in coverage))
        if observation is None and not items and not inventory_complete:
            # There is no registered exit to compare and no complete inventory
            # from which an outside-inventory contradiction could be inferred.
            # Keep supplied same-frame observations intact for diagnostics.
            return dict(applicable=False, contradicted=False, checks=[],
                        coverageComplete=False, observationStatus='source-inventory-incomplete')
        observations = observation.get('observations', []) if observation is not None else self._entry_observations(frame)
        matrix = np.asarray(row['matrixSourceToClient'], float)
        checks = []
        cardinal = {'左': 'W', '右': 'E', '上': 'N', '下': 'S'}
        for observed in observations:
            if not observed['reliable'] or not observed['direction'].get('reliable'):
                continue
            center = np.asarray(observed['icon']['center'])
            nearby = [p for p in items if np.linalg.norm(matrix[:2, :2]@p['center']+matrix[:2, 2]-center)
                      <= max(6., p['unit']*matrix[0, 0]*.2)]
            direction = cardinal[observed['direction']['direction']]
            applicable = [p for p in nearby if p['family'] == 'side' and p['directionKnown']]
            contradicted = bool(applicable and all(direction not in p['outwardDirections'] for p in applicable)
                                and len(applicable) == len(nearby))
            main_possible = False
            for item in items:
                if item['family'] != 'main':
                    continue
                main = next(p for p in item['localInstances'] if p['instance'] == item['sourceEntryId'])
                polygon = np.asarray(main['outline'])*item['unit']+item['center']
                polygon = (polygon@matrix[:2, :2].T+matrix[:2, 2]).astype(np.float32)
                if cv2.pointPolygonTest(polygon, tuple(map(float, center)), True) >= -6:
                    main_possible = True
            outside_inventory = inventory_complete and not nearby and not main_possible
            checks.append(dict(applicable=bool(applicable or outside_inventory),
                               contradicted=bool(contradicted or outside_inventory), mainFamilyPossible=main_possible,
                               sourceEntryIds=[p['sourceEntryId'] for p in nearby], direction=direction,
                               reason='exit-outside-complete-source-inventory' if outside_inventory else 'nearby-entry-direction'))
        return dict(applicable=any(c['applicable'] for c in checks),
                    contradicted=any(c['contradicted'] for c in checks), checks=checks,
                    coverageComplete=inventory_complete)

    def rank_entry(self, frame, floor=None, *, budget_seconds=None, candidate_map_ids=None):
        """Collect resumable passport source jobs at the existing entry point."""
        started = time.perf_counter()
        deadline = started + (2. if budget_seconds is None else max(0., float(budget_seconds)))
        work = self.prepare_rank_entry(frame, floor, deadline=deadline, candidate_map_ids=candidate_map_ids)
        while (job := work.next_job()) is not None:
            if time.perf_counter() >= deadline:
                break
            job.run()
        return work.finish(elapsed_ms=(time.perf_counter() - started) * 1000)

    def prepare_rank_entry(self, frame, floor=None, *, deadline=None, candidate_map_ids=None):
        from entry_passport_evidence import PassportEntryEvidence
        return PassportEntryEvidence(self, frame, floor, deadline=deadline, candidate_map_ids=candidate_map_ids)

    def _geometry(self, frame):
        from difficult_entry_identity import entry_geometry
        if self.geometry_provider is not None:
            try:
                return self.geometry_provider(frame)
            except Exception:
                # A provided observation is authoritative, including failure.
                return None
        return entry_geometry(frame)

    def _observe(self, frame):
        from single_map_structure import current_part
        context = self._geometry(frame)
        if context is None:
            return None, [], np.empty((0, 2), int)
        nodes = current_part(frame, context, 'nodes')
        supported = cv2.dilate(current_part(frame, context, 'observed').astype(np.uint8), np.ones((5, 5), np.uint8))
        ids = []
        for i, node in enumerate(nodes):
            if not node['valid']:
                continue
            ok = True
            for ray, length in zip(node['rays'], node['lengths']):
                p = np.rint(node['point'] + np.arange(2, min(12, int(length)))[:, None] * ray).astype(int)
                if not len(p) or not supported[p[:, 1], p[:, 0]].all():
                    ok = False
            if ok:
                ids.append(i)
        lookup = {old: new for new, old in enumerate(ids)}
        pairs = np.array([(lookup[i], lookup[nodes[i]['next']]) for i in ids if nodes[i]['next'] in lookup], int).reshape(-1, 2)
        return context, [nodes[i] for i in ids], pairs

    def rank(self, frame, floor=None, *, budget_seconds=2.0, map_id=None, candidate_map_ids=None):
        started = time.perf_counter()
        deadline = started + max(0., float(budget_seconds))
        if floor is not None and str(floor) not in self.floors:
            return [], dict(reason='unsupported-floor', complete=True)
        if deadline <= started:
            return [], dict(reason='side-budget-incomplete', complete=False)
        work = self.prepare_rank(frame, floor, map_id=map_id, deadline=deadline,
                                 candidate_map_ids=candidate_map_ids)
        while (job := work.next_job()) is not None:
            if time.perf_counter() >= deadline:
                break
            job.run()
        return work.finish(elapsed_ms=(time.perf_counter() - started) * 1000)

    def prepare_rank(self, frame, floor=None, *, map_id=None, deadline=None, candidate_map_ids=None):
        from side_entry_evidence import SideEntryEvidence
        return SideEntryEvidence(self, frame, floor, map_id=map_id, deadline=deadline,
                                  candidate_map_ids=candidate_map_ids)

    def _load_route(self, profile_id, map_id, floor, resource, cache=None):
        from nightmare_candidate import imread_unicode
        try:
            # An explicit loader is authoritative, including a failed load.
            # Never turn failure into success using a different source image.
            if self.route_loader is not None:
                return self.route_loader(profile_id, map_id, floor)
            if getattr(self, 'read_resource', None) is not None:
                return None
            path = (self.root / resource).resolve()
            key = (profile_id, map_id, str(floor), path)
            if cache is None:
                return imread_unicode(path, -1)
            if key not in cache:
                cache[key] = imread_unicode(path, -1)
            return cache[key]
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, cv2.error):
            return None

    def _entry_structure_variants(self, row, product):
        """Derived whole-footprint alternatives; original/unknown stay intact."""
        item = next((p for p in self.passports if p['mapId'] == row['mapId'] and p['floor'] == str(row['floor'])
                     and p['sourceEntryId'] == row['entryEvidence']['sourceEntryId']), None)
        if item is None:
            return []
        variants = [('source-present', product.clean_mask, None)]
        for room in item.get('optionalRooms', []):
            absent = product.clean_mask.copy()
            outline = np.rint(np.asarray(room['outline'])*item['unit']+item['center']).astype(np.int32)
            cv2.fillPoly(absent, [outline], 0)
            variants.append(('absent:'+room['instance'], absent, room['basis']))
        return variants

    def _verify_entry_walls(self, frame, row, context, product, *, _variants=None):
        """All observed wall fragments, independent of fitted-plane selection."""
        from single_map_structure import initial_wall_raster_validation, current_part
        references = self._entry_structure_variants(row, product) if _variants is None else _variants
        if not references:
            return dict(verified=False, reason='entry-passport-unavailable', variants=[])
        matrix = np.asarray(row['matrixSourceToClient'])
        model = np.r_[matrix[0, 0], matrix[:2, 2]]
        qlines = current_part(frame, context, 'lines')
        variants = []
        def check(name, reference, basis=None):
            result = initial_wall_raster_validation(qlines, reference, model, source_unknown=product.unknown_mask, source_wall_mask=product.known_boundary)
            variants.append(dict(name=name, result=result, basis=basis))
        for name, reference, basis in references:
            check(name, reference, basis)
        return dict(verified=any(v['result']['verified'] for v in variants), variants=variants,
                    scope='all current observed fragments, not only fitted passport planes')

    def refine_candidate(self, frame, row):
        """Refine a unique registered-floor proposal, then run the same validator."""
        from single_map_structure import partial_wall_geometry
        started=time.perf_counter()
        profile = self.profile_for_map(row['mapId'])
        shared_reference = None
        if self.shared_reference_provider is not None:
            try:
                shared_reference = self.shared_reference_provider(profile, row['mapId'], row['floor'])
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                shared_reference = None
            product = shared_reference
        else:
            product=self.structures.load(profile,row['mapId'],row['floor'])
        context=self._geometry(frame)
        if product is None or context is None:return row
        refined=partial_wall_geometry(frame,product.clean_mask,np.asarray(row['matrixSourceToClient']),
            _context=context,source_known_boundary=product.known_boundary,
            source_unknown=product.unknown_mask,source_wall_mask=product.known_boundary)
        if not refined.get('success'):return dict(row,continuousRefinementReason=refined.get('reason'),
            continuousRefinementMs=(time.perf_counter()-started)*1000)
        candidate=dict(row,matrixSourceToClient=np.asarray(refined['matrix']).tolist(),
            initialVerification=row.get('verification'),continuousRefinementReason=refined.get('reason'))
        candidate['verification']=self.verify(frame,candidate,context=context)
        candidate['continuousRefinementMs']=(time.perf_counter()-started)*1000
        return candidate

    def verify(self, frame, row, *, context=None, _route_cache=None):
        """Current-frame evidence only; the shared consumer must authorize display."""
        if self._side_door_contradicted(row):
            return dict(success=False, reason='side-door-boundary-contradiction')
        profile = self.profile_for_map(row['mapId'])
        shared_reference = None
        if self.shared_reference_provider is not None:
            try:
                shared_reference = self.shared_reference_provider(profile, row['mapId'], row['floor'])
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                shared_reference = None
            product = shared_reference
        else:
            product = self.structures.load(profile, row['mapId'], row['floor'])
        if product is None:
            return dict(success=False, reason='side-resource-unavailable')
        context = self._geometry(frame) if context is None else context
        if context is None:
            return dict(success=False, reason='side-observation-unavailable')
        references = self._entry_structure_variants(row, product) if row.get('entryEvidence') else [('source-present', product.clean_mask, None)]
        # A local turn match is only a proposal, just like an entrance match.
        # Its first display must satisfy the same observed-wall evidence as
        # reuse of that pose; regional averages can hide a contradicted opening.
        return self._verify_structure_references(frame, row, context, product, references,
                                                True, _route_cache, shared_reference=shared_reference)

    @staticmethod
    def _side_door_contradicted(row):
        constraint = row.get('sideDoorConstraint')
        if constraint is None:
            return False
        from side_entry_geometry import boundary_matches
        matrix = np.asarray(row['matrixSourceToClient'], float)
        return not boundary_matches(constraint['door'], constraint['observed'], matrix[0, 0],
                                    matrix[:2, 2], constraint['unit'])

    def verify_existing_pose(self, frame, map_id, floor, matrix, *, context=None, observation=None):
        """Validate only a caller-owned committed pose; never identify or fit.

        context/observation, when supplied, must belong to THIS frame. The
        caller retains identity, floor, generation and display authorization.
        """
        started = time.perf_counter()
        spans = {}
        def finish(result):
            return dict(result, scope='existing-pose-current-frame-only', identityVerified=False,
                        spansMs=spans, elapsedMs=(time.perf_counter()-started)*1000)
        try:
            matrix = np.asarray(matrix, float)
            valid = (matrix.shape == (3, 3) and np.isfinite(matrix).all() and matrix[0, 0] > 0
                     and np.allclose(matrix[2], [0, 0, 1])
                     and np.allclose(matrix[:2, :2], np.eye(2)*matrix[0, 0]))
        except (TypeError, ValueError):
            valid = False
        if not valid or str(floor) not in self.floors:
            return finish(dict(success=False, reason='existing-pose-invalid-input'))
        row = dict(mapId=map_id, floor=str(floor), matrixSourceToClient=matrix.tolist())
        profile = self.profile_for_map(map_id)
        shared_reference = None
        if self.shared_reference_provider is not None:
            try:
                shared_reference = self.shared_reference_provider(profile, map_id, str(floor))
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                shared_reference = None
            product = shared_reference
        else:
            product = self.structures.load(profile, map_id, str(floor))
        if product is None:
            return finish(dict(success=False, reason='side-resource-unavailable'))
        checks = None
        def check_current_exits():
            nonlocal checks
            observed_at = time.perf_counter()
            try:
                checks = self.check_entry_evidence(frame, row, observation)
            except Exception:
                return dict(success=False, reason='existing-pose-entry-observation-unavailable')
            finally:
                spans['entryObservation'] = (time.perf_counter()-observed_at)*1000
            if checks['contradicted']:
                return dict(success=False, reason='entry-other-current-exit-contradiction', allObservedExits=checks)
            return None
        # Reuse already-observed evidence immediately. Otherwise reject a bad
        # pose on geometry/author first; no successful pose bypasses exits.
        if observation is not None:
            failure = check_current_exits()
            if failure is not None:
                return finish(failure)
        geometry_at = time.perf_counter()
        context = self._geometry(frame) if context is None else context
        spans['geometry'] = (time.perf_counter()-geometry_at)*1000
        if context is None:
            return finish(dict(success=False, reason='side-observation-unavailable', allObservedExits=checks))
        # The map is already caller-selected. Gather its registered alternatives,
        # without inventing an observation/sourceEntryId or an identity row.
        references = [('source-present', product.clean_mask, None)]
        outlines = {}
        for item in self.passports:
            if item['mapId'] != map_id or item['floor'] != str(floor):
                continue
            for room in item.get('optionalRooms', []):
                outline = np.rint(np.asarray(room['outline'])*item['unit']+item['center']).astype(np.int32)
                instance = room['instance']
                if instance in outlines:
                    if not np.array_equal(outlines[instance], outline):
                        return finish(dict(success=False, reason='existing-pose-variant-source-conflict', allObservedExits=checks))
                    continue
                outlines[instance] = outline
                absent = product.clean_mask.copy()
                cv2.fillPoly(absent, [outline], 0)
                references.append(('absent:'+instance, absent, room['basis']))
        validated_at = time.perf_counter()
        result = self._verify_structure_references(frame, row, context, product, references, True,
                                                   shared_reference=shared_reference)
        spans['structureAndAuthor'] = (time.perf_counter()-validated_at)*1000
        if result.get('success') and checks is None:
            failure = check_current_exits()
            if failure is not None:
                result = dict(result, **failure)
        return finish(dict(result, allObservedExits=checks,
                           entryObservationStatus=(checks.get('observationStatus', 'checked') if checks is not None else
                               'unavailable' if 'entryObservation' in spans else 'skipped-pose-failed'),
                           passportAvailable=not bool(self.passport_error), optionalInstances=list(outlines)))

    def _verify_structure_references(self, frame, row, context, product, references, check_walls, route_cache=None, shared_reference=None):
        """One common numerical path for entry proposals and committed poses."""
        from single_map_structure import regional_validation, _static_validation_context
        from nightmare_candidate import selected_route_current_frame_residual
        matrix = np.asarray(row['matrixSourceToClient'], float)
        local_fit = (row.get('entryEvidence') or {}).get('localGeometryVerified') is True
        entry_walls = self._verify_entry_walls(frame, row, context, product, _variants=references) if check_walls and not local_fit else None
        validation_context = _static_validation_context(frame, context, matrix, product.unknown_mask) if not local_fit else None
        structural_variants = []
        for name, reference, basis in ([] if local_fit else references):
            projected = cv2.warpAffine(reference, matrix[:2], frame.shape[1::-1], flags=cv2.INTER_NEAREST)
            region = regional_validation(frame, context['query'], projected, _context=validation_context)
            walls = next(v['result'] for v in entry_walls['variants'] if v['name'] == name) if entry_walls else None
            structural_variants.append(dict(name=name, basis=basis, regions=region, walls=walls,
                verified=bool(region['regionalEdgeVerified'] and (walls is None or walls['verified']))))
        compatible = [v for v in structural_variants if v['verified']]
        regions = (compatible or structural_variants or [dict(regions=dict(regionalEdgeVerified=False))])[0]['regions']
        residual = None
        if compatible or local_fit:
            profile = self.profile_for_map(row['mapId'])
            route = None
            if shared_reference is not None:
                # The shared product was selected before any legacy product or
                # route load. Keep this exact product for geometry and the
                # current-frame residual; do not reread or replace it.
                pass
            else:
                route = self._load_route(profile, row['mapId'], row['floor'],
                    product.entry.get('routeResource', product.entry.get('sourceLayerPath')), route_cache)
            if route is None and shared_reference is None:
                return dict(success=False, reason='side-shared-structure-unavailable')
            if route is not None and getattr(route, 'shape', ())[:2] != product.clean_mask.shape:
                return dict(success=False, reason='side-resource-unavailable')
            visible = None
            if self.author_visible_provider is not None:
                try:
                    # Pass base observation, not a pose-specific unknown mask.
                    visible = self.author_visible_provider(frame, context=context)
                except Exception:
                    return dict(success=False, reason='side-author-observation-unavailable')
                if not isinstance(visible, tuple) or len(visible) != 3:
                    # None would silently trigger the residual's own extractor.
                    return dict(success=False, reason='side-author-observation-unavailable')
            residual = selected_route_current_frame_residual(frame, route, matrix, maximum_dimension=960,
                                                             route_profile_id=profile,
                                                             reference_override=(None if shared_reference is not None else product.validation_mask),
                                                             shared_reference=shared_reference,
                                                             visible_override=visible)
        return dict(success=bool((compatible or local_fit) and residual and residual.get('verified')),
                    reason='side-current-frame-evidence', regions=regions, residual=residual,
                    validationPolicy=('local-fit-and-shared-structure' if local_fit and shared_reference is not None
                                      else 'local-fit-and-author' if local_fit
                                      else 'whole-structure-and-shared-structure' if shared_reference is not None
                                      else 'whole-structure-and-author'),
                    **({'entryWalls': entry_walls, 'structureVariants': structural_variants,
                        'compatibleStructureVariants': [v['name'] for v in compatible],
                        **({'referenceChannel': 'typed-shared-structure-validation-mask'}
                           if shared_reference is not None else
                           {'authorReference': 'unchanged original-author validation_mask; not a derived structure variant'})}
                       if entry_walls is not None else {}),
                    matrixSourceToClient=matrix.tolist())


DifficultSideEntryIdentity = MapLocalEntryIdentity
