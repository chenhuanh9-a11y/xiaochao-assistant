"""Resolve the authoritative 24/72 manifest through bounded known source paths."""
import csv
import json
from pathlib import Path
import argparse
BASE = SUJU = GAME = F3 = None

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))

def main():
    global BASE, SUJU, GAME, F3
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--suju-root',type=Path,required=True)
    parser.add_argument('--game-root',type=Path,required=True)
    parser.add_argument('--baseline-references',type=Path,required=True)
    args=parser.parse_args()
    BASE=args.workspace.resolve();SUJU=args.suju_root.resolve();GAME=args.game_root.resolve();F3=args.baseline_references.resolve()
    BASE.mkdir(parents=True,exist_ok=True)
    plan=read(SUJU/'integration-plan.json')
    upstream=read(Path(plan['originalCatalog']))
    upstream_ids={r['Id'] for r in upstream['Maps']}
    old=read(BASE/'coverage-snapshot.json')['registered']['layoutBindings']
    legacy={values[0]:map_id for map_id,values in old.items()}
    roots=[GAME/f'known-batch-{i}' for i in range(1,6)]+[GAME/'known-layout-structures-last-r2',GAME/'known-layout-structures-last',GAME/'known-layout-structures']
    rows=[];gaps=[];mapping=[]
    for target in plan['entries']:
        layout=target['sourceLayout'];guid=target['mapId']
        assert guid in upstream_ids
        runtime=legacy.get(layout) or guid
        mapping.append(dict(layout=layout,targetGuid=guid,title=target['title'],legacyRuntimeMapId=legacy.get(layout),
            candidateRuntimeMapId=runtime,runtimeIdBasis='existing-F3-layout-binding' if layout in legacy else 'existing-authoritative-Map-UUID; new candidate registration, not a guessed numeric suffix'))
        for floor in target['floors']:
            source=floor['TilemapSource'];world=source['WorldFloor'];label=source['FloorKey'];key={'B1':'B1','1F':'1','2F':'2'}[label]
            stem=f'level_{layout}_floor_{world}'
            candidates=[(r/f'{stem}_60px.png',r/f'{stem}.json') for r in roots if (r/f'{stem}_60px.png').is_file() and (r/f'{stem}.json').is_file()]
            canonical=Path(floor['canonicalImage']);old_meta=read(canonical.with_name('source.json'))
            chosen=None;disagreements=[]
            for png,meta_path in candidates:
                meta=read(meta_path)
                ok=(str(meta['levelId'])==layout and meta['floor']==world and meta['targetPixelsPerVoxel']==60
                    and meta['bounds']==old_meta['render']['bounds'] and meta['placements']==old_meta['render']['placements'])
                if ok:
                    chosen=(png,meta_path,meta);break
                disagreements.append(str(meta_path))
            row=dict(targetGuid=guid,targetTitle=target['title'],sourceLayout=layout,
                runtimeMapId=runtime,legacyRuntimeMapId=legacy.get(layout),
                runtimeRegistrationStatus='legacy-registered' if layout in legacy else 'missing-in-F3; candidate uses existing stable UUID',
                floor=key,floorKey=label,sourceWorldFloor=world,
                canonicalSource=str(canonical),canonicalSourceExists=canonical.is_file(),
                displaySource=floor['derivedDisplay'],displayExists=Path(floor['derivedDisplay']).is_file(),
                displayRegistration=str(Path(floor['derivedDisplay']).parent/'registration.json'),
                displaySnapshot=floor['sourceSnapshot'],nativeSourcePNG=str(chosen[0]) if chosen else None,
                nativeSourceMetadata=str(chosen[1]) if chosen else None,
                nativeSourceBound=bool(chosen),nativeToSujuDisplay='identity via equal source layout/floor/bounds/placements/60px' if chosen else None,
                nativeCanvasWH=[source['RenderedPixelWidth'],source['RenderedPixelHeight']],
                boundsEqual=bool(chosen),placementsEqual=bool(chosen),
                legacyIdentityRegistered=layout in legacy,
                typedPreviouslyDelivered=(layout=='104001' and key=='B1'),
                nativeAdapterPreviouslyDelivered=(layout=='104001' and key=='B1'),
                replayEvidence='historical-synthetic-manual-B1 only; not full24 competition' if layout=='104001' and key=='B1' else 'not verified in this T batch',
                semanticGatesAvailable=False,passportAvailable=False,knownWallMaskAvailable=False,
                sourceMissingRoomFloors=(chosen[2].get('diagnostics',{}).get('skippedRoomFloors',[]) if chosen else []),
                candidateMetadataDisagreements=disagreements)
            if not chosen:
                gap=dict(layout=layout,floor=key,reason='no known current renderer pair matched authoritative canonical geometry',
                    expectedNames=[f'{stem}_60px.png',f'{stem}.json'],searchedSourceRoots=[str(r) for r in roots],
                    recovery='restore or render only this bound layout/worldFloor from current game catalog; do not copy another layer')
                gaps.append(gap)
            rows.append(row)
    result=dict(authoritativePlan=str(SUJU/'integration-plan.json'),authoritativeMapsCatalog=plan['originalCatalog'],
        scope='24 maps/72 real floors target; source resolution only, no identity accuracy or runtime acceptance',
        targetMaps=len(plan['entries']),targetLayers=len(rows),boundNativeSourceLayers=sum(r['nativeSourceBound'] for r in rows),
        displayLayersPresent=sum(r['displayExists'] for r in rows),legacyRegisteredMaps=len(legacy),
        missingRuntimeMaps=[m for m in mapping if m['legacyRuntimeMapId'] is None],mapping=mapping,entries=rows,gaps=gaps,
        noNumericRuntimeIDsInvented=True,noSourceImagesDecoded=True,noFullDiskScan=True)
    (BASE/'target-24-native-plan.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    cols=['targetGuid','targetTitle','sourceLayout','runtimeMapId','legacyRuntimeMapId','runtimeRegistrationStatus','floor','floorKey','sourceWorldFloor',
        'nativeSourceBound','nativeSourcePNG','nativeSourceMetadata','displayExists','displaySource','displayRegistration','legacyIdentityRegistered',
        'typedPreviouslyDelivered','nativeAdapterPreviouslyDelivered','replayEvidence','semanticGatesAvailable','passportAvailable','knownWallMaskAvailable']
    with (BASE/'target-24-floor-coverage.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    print(json.dumps({k:v for k,v in result.items() if k not in {'mapping','entries'}},ensure_ascii=True,indent=2))

if __name__=='__main__':main()
