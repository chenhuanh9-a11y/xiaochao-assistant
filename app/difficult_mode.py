"""Difficult resources on the shared candidate, coordinate and display interfaces."""
import json
import os
import time
import cv2
from pathlib import Path
import numpy as np
from nightmare_candidate import NightmarePack, NightmareMap, NightmareCandidateEngine
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters, CANONICAL_SCHEMA
from author_route_catalog import AuthorRouteCatalog, AuthorRouteProfile, AuthorRouteVariant
from author_structure_resources import AuthorStructureResourceStore


def difficult_key(map_id, floor):
    """Normalize external names once, at the difficult resource boundary."""
    import re
    match = re.fullmatch(r'(?:difficult-)?map-?(\d{1,2})(-2)?', str(map_id))
    layer = str(floor).upper().removeprefix('F')
    return (f'difficult-map-{int(match[1]):02d}{match[2] or ""}' if match else str(map_id), layer)


from map_identity_engine import (MapIdentityEngine, entry_rank_diagnostics as difficult_rank_diagnostics,
    verified_entry_candidate, verified_side_candidate, entry_passport_fallback_allowed)


class DifficultStructureStore:
    def __init__(self, resource_root):
        root = Path(resource_root) / 'references'
        self.legacy = AuthorStructureResourceStore(root/'difficult-author-structure-index.json', root/'difficult-structure')
        self.tilemaps = AuthorStructureResourceStore(root/'difficult-tilemaps/index.json', root/'difficult-tilemaps')
        self.error = self.legacy.error
        self.index_path = self.legacy.index_path
        self.resource_root = self.legacy.resource_root
        self.entries = {**self.legacy.entries, **self.tilemaps.entries}

    def load(self, profile_id, map_id, floor, **kwargs):
        map_id, floor = difficult_key(map_id, floor)
        # An imported route has a different canvas. Never substitute an old
        # structure when its Tilemap is corrupt; the ordinary online fallback
        # can still process the selected author image.
        key = self.tilemaps._key(profile_id, map_id, floor, kwargs.get('variant_id'))
        store = self.tilemaps if key in self.tilemaps.entries else self.legacy
        return store.load(profile_id, map_id, floor, **kwargs)

    def load_for_route(self, route, profile_id, map_id, floor, **kwargs):
        if not isinstance(route, np.ndarray):return None
        return self.load(profile_id, map_id, floor, route_shape=route.shape[:2], **kwargs)


def validate_difficult_native_resources(resource_root):
    """Read-only source/frozen diagnostic through the production store/locator."""
    from difficult_entry_identity import DifficultEntryIdentity
    store=DifficultStructureStore(resource_root)
    tilemaps=store.tilemaps
    locator=DifficultEntryIdentity(tilemaps.resource_root,structure_store=store)
    rows=[];failures=[];loaded=0;consumed=0
    for key,entry in sorted(tilemaps.entries.items()):
        profile,map_id,variant,floor=key
        row=dict(mapId=map_id,floor=floor,resourcePath=str(tilemaps._path_for(entry)),
                 loaded=False,nativeIndexConsumed=False,segmentCount=0)
        product=store.load(profile,map_id,floor,variant_id=variant,
                           route_shape=tuple(entry.get('sourceShape',())))
        if product is None:
            failures.append(f'{map_id}/{floor}:resource-load-failed')
        elif product.native_wall_segments is None:
            loaded+=1;row['loaded']=True
            # Report a missing optional optimization instead of doing an
            # expensive fallback that would hide a packaging omission.
            failures.append(f'{map_id}/{floor}:native-index-unavailable')
        else:
            loaded+=1;row['loaded']=True
            lines=locator.native_wall_lines(product,map_id,floor)
            row['segmentCount']=len(lines)
            row['nativeIndexConsumed']=lines is product.native_wall_segments
            if row['nativeIndexConsumed']:consumed+=1
            else:failures.append(f'{map_id}/{floor}:native-index-not-consumed')
        rows.append(row)
        # This diagnostic visits every floor; no gameplay cache or pose lives
        # beyond this function, so release each checked static product.
        locator._wall_lines.clear()
    return dict(success=not failures and not tilemaps.error and not locator.error
                and len(tilemaps.entries)==60 and loaded==60 and consumed==60,
                indexPath=str(tilemaps.index_path),resourceRoot=str(tilemaps.resource_root),
                indexEntryCount=len(tilemaps.entries),loadedEntryCount=loaded,
                nativeIndexConsumedCount=consumed,failedEntries=failures,
                error=tilemaps.error or locator.error,entries=rows)


class DifficultPack(NightmarePack):
    mode='difficult'
    floors=('1','2')

    def __init__(self, resource_root, asset_root):
        self.resource_root=Path(resource_root)
        self.manifest_path=Path(resource_root)/'references/difficult-runtime.json'
        self.raw=json.loads(self.manifest_path.read_text(encoding='utf8'))
        if self.raw.get('mode')!='difficult':raise ValueError('Difficult resource mode mismatch')
        self.source_root=Path(asset_root)
        self.tilemap_root=Path(resource_root)/'references/difficult-tilemaps'
        try:
            tilemap_index=json.loads((self.tilemap_root/'index.json').read_text(encoding='utf8'))
            self.tilemap_entries={(e['mapId'],e['floor']):e for e in tilemap_index['entries']}
        except (OSError,ValueError,KeyError,TypeError):
            self.tilemap_entries={}
        self.pack_id=self.raw['packId'];self.display_name='困难 · 展十'
        self.default_entry_floor=1
        self.maps=[]
        for row in self.raw['maps']:
            self.maps.append(NightmareMap(id=row['id'],name=row['name'],direction=row['direction'],
                door_count=0,shape=row['shape'],side_door_crop=tuple(row['previewBox']),
                one_flow=Path(resource_root)/row['oneFlowResource'],
                layers={f:(self.tilemap_root/self.tilemap_entries[(row['id'],f)]['routeResource']
                    if (row['id'],f) in self.tilemap_entries else self.source_root/p)
                    for f,p in row['layers'].items()}))
        self.by_id={m.id:m for m in self.maps}
        self.metadata={m['id']:m for m in self.raw['maps']}

    def validate(self):
        missing=[str(p) for m in self.maps for p in [m.one_flow,*m.layers.values()] if not p.is_file()]
        errors=[]
        manifest=self.source_root/'manifest.json'
        if not manifest.is_file():missing.append(str(manifest))
        else:
            try:
                source=json.loads(manifest.read_text(encoding='utf8'))
                if source.get('mode')!='difficult':raise ValueError('difficult source manifest mode mismatch')
                for row in source['maps']:
                    if row['manualReferenceStatus']=='clean-reference':
                        path=self.source_root/row['manualReference']
                        if not path.is_file():missing.append(str(path))
            except (OSError,ValueError,KeyError,TypeError) as error:
                errors.append(f'{manifest}: {error}')
        for path in (self.resource_root/'references/difficult-exit-icon.json',
                     self.resource_root/'references/difficult-ui-controls.npz'):
            if not path.is_file():missing.append(str(path))
        return dict(success=not missing and not errors and bool(self.maps),mapCount=len(self.maps),
            layerCount=sum(len(m.layers) for m in self.maps),sourceRoot=str(self.source_root),missing=missing,
            errors=errors,scope='resource-cards-and-engine-inputs; unique-layout-identities-not-implied')


class DifficultCatalog(AuthorRouteCatalog):
    def __init__(self,pack):
        self.default_profile_id='zhan10-speed';self.status='candidate'
        self.catalog_path=pack.manifest_path
        variants={m.id:(AuthorRouteVariant('zhan10-speed',m.id,'variant-01','速刷路线',m.layers,
                  'resource-card','current-frame-required'),) for m in pack.maps}
        self.profiles={'zhan10-speed':AuthorRouteProfile('zhan10-speed','展十 · 困难速刷路线','展十',
            '困难速刷',True,True,True,variants)}
        self._raw={'profiles':[]}


class DifficultCoordinateAdapters(AuthorCoordinateAdapters):
    def __init__(self,pack):
        self.path=pack.manifest_path;self.schema=CANONICAL_SCHEMA
        self.canonical_pack_id=pack.pack_id;self.coordinate_reference_profile_id='zhan10-speed'
        self.policy=dict(requiresCurrentFrameGeometryFit=True,authorizesMapIdentity=False,authorizesCrossFloorProjection=False)
        identity=np.eye(3).tolist()
        self.maps={m.id:{'floors':{f:{'matrixCanonicalToLegacyRecognition':identity} for f in m.layers}} for m in pack.maps}
        self.profiles={'zhan10-speed':{'maps':{m.id:{'floors':{f:dict(usableForCurrentFrameRendering=True,
            crossFloorProjectionAllowed=False,matrixProfileToCanonical=identity,
            evidence={'algebraicallyExact':True,'basis':'same-original-author-rgba-canvas'}) for f in m.layers}} for m in pack.maps}}}
        self.canonical_cross_floor=pack.raw.get('crossFloorSearchCandidates',{})
        self.tilemap_entries=pack.tilemap_entries
        try:
            self.difficult_floor_relations=json.loads((pack.tilemap_root/'floor-relations.json').read_text(encoding='utf8'))['maps']
        except (OSError,ValueError,KeyError,TypeError):
            self.difficult_floor_relations={}
        for (map_id,floor),entry in pack.tilemap_entries.items():
            matrix=np.asarray(entry['matrixAuthorToMap'],dtype=float)
            self.maps[map_id]['floors'][floor]['matrixCanonicalToLegacyRecognition']=np.linalg.inv(matrix).tolist()
            self.profiles['zhan10-speed']['maps'][map_id]['floors'][floor].update(
                matrixProfileToCanonical=matrix.tolist(),
                evidence={'algebraicallyExact':True,'basis':'native-crop-to-floor-local-grid-origin',
                          'sourceCommit':entry['sourceCommit'],'cropOffset':entry['cropOffset']})
        self._raw=dict(schema=self.schema,policy=self.policy,maps=self.maps,profiles=self.profiles)

    def canonical_cross_floor_search_seed(self,map_id,from_floor,to_floor,source_canonical_to_client,*,authorization_profile_id=None):
        """Source-structure alignment is a prior, never a player-accepted pose."""
        row=self.difficult_floor_relations.get(map_id,{})
        if ({str(from_floor),str(to_floor)}!={'1','2'} or not row.get('usable')
            or authorization_profile_id not in (None,'zhan10-speed')):return None
        try:
            relation=np.asarray(row['canonicalF2ToF1'],float)
            source=np.asarray(source_canonical_to_client,float)
            if relation.shape!=(3,3) or source.shape!=(3,3) or not np.isfinite(relation).all() or not np.isfinite(source).all():return None
            if str(from_floor)=='2':relation=np.linalg.inv(relation)
        except (ValueError,TypeError,KeyError,np.linalg.LinAlgError):return None
        return dict(mapId=map_id,fromFloor=str(from_floor),toFloor=str(to_floor),
            matrixSourceToClient=(source@relation).tolist(),sourceMatrixToClient=source.tolist(),
            targetToSourceMatrix=relation.tolist(),confidence=.85,runtimeVerified=False,
            currentFrameDisplayValidated=False,directProjectionAllowed=False,zeroExplorationDisplayAllowed=False,
            profileMappingVerified=False,playerReviewedProjectionTrial=False,requiresStableTargetFloor=True,
            boundedCrossFloorOnly=True,profileCurrentFrameSearchSeed=True,requiresCurrentFrameDisplayValidation=True,
            mappingProfileId=self.canonical_pack_id,matrixCoordinatePackId=self.canonical_pack_id,
            matrixCoordinateProfileId=self.canonical_pack_id,reviewStatus='offline-source-structure-verified',
            transferSource='difficult-dual-floor-source-correspondence',evidenceLevel=row['evidenceLevel'])

    def regional_cross_floor_author_relation_for_source(self,map_id,from_floor,to_floor,source_author_point):
        """Select one offline room-group relation by the observed source player point."""
        if {str(from_floor),str(to_floor)}!={'1','2'}:return None
        row=self.difficult_floor_relations.get(map_id,{})
        if row.get('usable'):return None
        entry=self.tilemap_entries.get((map_id,str(from_floor))) or {}
        point=np.asarray(source_author_point,dtype=float)
        if point.shape!=(2,) or not np.isfinite(point).all():return None
        containing=[]
        for region in entry.get('regions',[]):
            bounds=region.get('bounds') or []
            if (len(bounds)==4 and bounds[0]<=point[0]<=bounds[2]
                and bounds[1]<=point[1]<=bounds[3]):
                containing.append(str(region.get('id')))
        if len(containing)!=1:return None
        matching=[]
        for region in row.get('regions',[]):
            if not region.get('usable'):continue
            source_id=(region.get('fromRegion') if str(from_floor)=='1'
                else region.get('toRegion'))
            if str(source_id)!=containing[0]:continue
            try:
                relation=np.asarray(region['authorF2ToF1'],dtype=float)
                if str(from_floor)=='1':relation=np.linalg.inv(relation)
                if relation.shape!=(3,3) or not np.isfinite(relation).all():continue
            except (KeyError,TypeError,ValueError,np.linalg.LinAlgError):continue
            matching.append((region,relation))
        if len(matching)!=1:return None
        region,relation=matching[0]
        return dict(matrixSourceAuthorToTargetAuthor=relation.tolist(),
            fromRegion=region.get('fromRegion'),toRegion=region.get('toRegion'),
            sourceRegion=containing[0],sourceAuthorPoint=point.tolist(),
            evidenceLevel=row.get('evidenceLevel'),directProjectionAllowed=False,
            currentFrameVerified=False)


class DifficultCandidateEngine(MapIdentityEngine):
    """Compatibility constructor for difficult resource configuration."""
    def __init__(self, *args, resource_root, **kwargs):
        from difficult_entrance import DifficultEntrance
        from side_door_recognition import SideDoorRecognizer
        from difficult_grid import DifficultGridLocator
        pack = args[0] if args else kwargs['pack']
        icon = Path(resource_root)/'references/difficult-exit-icon.json'
        entrance = DifficultEntrance(pack.source_root/'manifest.json',pack.source_root,icon)
        detector = SideDoorRecognizer(icon)
        def advisory(frame):
            result = entrance.rank(frame, detector.find_anchors(frame))
            return dict(result, candidates=[dict(row,mapId='difficult-'+row['mapId'])
                for row in result.get('candidates',[])])
        super().__init__(*args, identity_root=pack.tilemap_root,
            identity_floors=pack.floors, exit_icon_path=icon,
            entry_advisory=advisory, **kwargs)
        self.entrance = entrance
        self.exit_detector = detector
        self.grid_locator = DifficultGridLocator(pack.tilemap_root,pack.tilemap_entries)

    def pose_index_candidates(self,frame,map_id,floor,*,cancelled=None,context=None):
        return self.grid_locator.propose(frame,map_id,floor,cancelled=cancelled,context=context)
