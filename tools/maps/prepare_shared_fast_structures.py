"""Offline preparation through the same installed shared-reference providers."""
from pathlib import Path
from dataclasses import replace
import argparse
import hashlib
import json
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'experiments' / 'qt_quick')]
from client_providers import SharedReferenceProvider
from fast_structure_alignment import prepare,load_prepared,prepared_path_for


def _local(root, relative):
    path = (root / str(relative)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'Resource escapes explicit root: {relative}')
    return path


def _side_entries(root, mode, profile, pack, resources):
    # Match the original optional locator boundary. A missing/broken rank index
    # must not disable independently validated typed structure preparation.
    # When readable, retain its per-floor binding checks in the shared loader.
    from difficult_side_entry_identity import MapLocalEntryIdentity
    try:
        return MapLocalEntryIdentity(root, mode=mode, profile_id=profile,
            floors=pack.floors, structure_store=resources).entries
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return []


def references(root, modes):
    """Build typed resources, not a rank engine; decode pixels only on demand.

    Selection remains after resource-context construction, as in the original
    tool. Shared version/coordinate checks and difficult inventory checks are
    retained, without adding the live client's full ranking-readiness gate.
    """
    from author_route_catalog import AuthorRouteCatalog
    from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters
    from nightmare_candidate import NightmarePack
    from shared_structure_reference import SharedStructureResourceStore
    from shared_structure_context import load_shared_version_context

    root = Path(root).resolve()
    for mode in modes:
        native = None
        if mode == 'nightmare':
            manifest = _local(root, 'references/nightmare-map-pack.json')
            raw = json.loads(manifest.read_text(encoding='utf-8'))
            for row in raw['maps']:
                if row.get('resourceBase') != 'resource-root':
                    raise ValueError('Shared maps must bind the explicit resource root')
                for relative in [row['oneFlow'], *row['layers'].values()]:
                    _local(root, relative)
            pack = NightmarePack(manifest)
            resources = SharedStructureResourceStore(
                _local(root, 'references/nightmare-shared-structure-index.json'), root)
            if resources.error:
                raise ValueError(resources.error)
            adapters = AuthorCoordinateAdapters(
                _local(root, 'references/nightmare-canonical-coordinate-pack.json'))
            try:
                catalog = AuthorRouteCatalog(
                    _local(root, 'references/nightmare-author-route-catalog.json'), asset_base=root)
            except (OSError, ValueError, KeyError, TypeError):
                catalog = None
            side = _side_entries(_local(root, 'references/nightmare-entries'),
                                 mode, 'zhan10-speed', pack, resources)
            versions = load_shared_version_context(root, pack, resources, adapters, catalog, side)
        elif mode == 'difficult':
            from difficult_native_context import DifficultNativeContext
            from difficult_native_identity import NativeDifficultPack
            interfaces = json.loads(_local(root, 'difficult-native-manifest.json').read_text(
                encoding='utf-8'))['runtimeInterfaces']
            if 'identityPack' in interfaces:
                native = DifficultNativeContext(root)
                pack = NativeDifficultPack(root, interfaces['identityPack'], display_context=native)
                native.validate_identity_pack(pack)
                side = _side_entries(native.local(interfaces['identityEntries']),
                                     mode, 'shared-structure', pack, native.resources)
            else:
                # Retain the original declared legacy-identity/native-display
                # adapter, including its existing inventory and bridge checks.
                from difficult_mode import DifficultPack
                pack = DifficultPack(root, root / 'assets/difficult-maps')
                check = pack.validate()
                if not check['success']:
                    raise ValueError(f'Difficult resources incomplete: {check}')
                native = DifficultNativeContext(root, pack)
                side = []
            resources = native.resources
            versions = load_shared_version_context(root, native.version_pack, resources,
                native.adapters, native.catalog, side, neutral_relative=native.neutral_relative,
                author_profile_id='zhan10-speed', entry_root_relative='')
            native.validate_versions(versions)
        else:
            raise ValueError(f'Unknown game mode: {mode}')
        provider = SharedReferenceProvider(root, resources, versions,
            legacy_reference_loader=native.legacy_reference if native is not None else None)
        for item in pack.maps:
            for floor in item.layers:
                yield mode, item.id, str(floor), provider


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('resource_root',type=Path)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--mode',choices=('nightmare','difficult'),action='append')
    parser.add_argument('--select',action='append',default=[],metavar='MAP/FLOOR')
    args=parser.parse_args()
    root=args.resource_root.resolve();output_root=args.output_root.resolve()
    if root==output_root:
        raise ValueError('Use an independent output root; source packages remain read-only')
    selected={tuple(value.rsplit('/',1)) for value in args.select}
    if any(len(key)!=2 for key in selected):
        raise ValueError('Selection must be MAP/FLOOR')
    seen=set();rows=[]
    for mode,map_id,floor,provider in references(root,args.mode or ('nightmare','difficult')):
        if selected and (map_id,floor) not in selected:
            continue
        reference=provider('shared-structure',map_id,floor)
        if reference is None:
            raise ValueError(f'Shared reference unavailable: {mode}/{map_id}/{floor}')
        target_reference=replace(reference,resource_root=output_root)
        target=prepared_path_for(target_reference)
        status='existing-matching'
        if target.exists():
            if load_prepared(target_reference) is None:
                raise ValueError(f'Existing prepared file has another binding: {target}')
        else:
            result=prepare(reference.clean_mask)
            target.parent.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(target,distance=result.distance,chains=result.chains,rays=result.rays,
                pitch=result.pitch,shape=result.shape,sourceMetadata=json.dumps(reference.metadata,sort_keys=True))
            status='generated'
        loaded=load_prepared(target_reference)
        if loaded is None:
            raise ValueError(f'Prepared readback rejected: {target}')
        seen.add((map_id,floor))
        rows.append(dict(mode=mode,mapId=map_id,floor=floor,status=status,
            sourceReference=str(reference.path),sourceKind=reference.metadata.get('sourceKind'),
            sourceClass=reference.metadata.get('sourceClass'),shape=list(reference.shape),
            knownBoundary=reference.known_boundary is not None,unknown=reference.unknown_mask is not None,
            nativeWalls=reference.native_wall_segments is not None,
            prepared=str(target),bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            distanceDtype=str(loaded.distance.dtype),sourceMetadata=reference.metadata))
        print(json.dumps({k:rows[-1][k] for k in ('mapId','floor','status','bytes','prepared')},ensure_ascii=False),flush=True)
    if selected-seen:
        raise ValueError(f'Selected bindings not declared by providers: {sorted(selected-seen)}')
    report=dict(resourceRoot=str(root),outputRoot=str(output_root),modes=args.mode or ['nightmare','difficult'],
        scope='Offline reference preparation/readback only; no rank/solver/GUI. Known/unknown/wall channels remain in runtime providers.',rows=rows)
    output_root.mkdir(parents=True,exist_ok=True)
    receipt=output_root/'generation-result.json'
    receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert json.loads(receipt.read_text(encoding='utf-8'))==report
    print(json.dumps(dict(success=True,generated=sum(row['status']=='generated' for row in rows),receipt=str(receipt))))


if __name__=='__main__':
    main()
