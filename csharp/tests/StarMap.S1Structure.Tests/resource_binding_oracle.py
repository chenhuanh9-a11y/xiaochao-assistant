"""Fresh resource evidence clone, validated by the unchanged original S1NativeContext."""
import argparse
import json
from pathlib import Path
import shutil
import sys


def main():
    p=argparse.ArgumentParser()
    for key in ('repo','resources','input','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    a.output.mkdir(parents=True)
    description=json.loads(a.input.read_text(encoding='utf-8'))
    mode=description['mode']
    source=a.resources/'seasons'/'s1'/mode
    copied_root=a.output/'resources'
    copied_mode=copied_root/'seasons'/'s1'/mode
    copied_mode.mkdir(parents=True)
    manifest_path=source/'native-map-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    for row in manifest['files']:
        path=Path(row['path'])
        if path.is_absolute() or '..' in path.parts:raise ValueError('Non-local fixture source')
        target=copied_mode/path
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/path,target)
    copied_manifest=copied_mode/'native-map-manifest.json'
    shutil.copy2(manifest_path,copied_manifest)
    sys.path.insert(0,str(a.repo/'app'))
    from s1_native_resources import S1NativeContext
    # Actual original native resource constructor, not a reconstructed predicate.
    positive=S1NativeContext(copied_mode,mode)
    coordinate_relative=manifest['runtimeInterfaces']['coordinatePack']
    coordinate_path=copied_mode/coordinate_relative
    before=json.loads(coordinate_path.read_text(encoding='utf-8'))
    manifest['files']=[row for row in manifest['files'] if row['path']!=coordinate_relative]
    copied_manifest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    def rejected():
        try:S1NativeContext(copied_mode,mode)
        except ValueError as error:return True,str(error)
        return False,None
    missing_rejected,missing_error=rejected()
    changed=dict(before)
    changed['canonicalPackId']=before['canonicalPackId']+'-unlisted-coordinate-regression'
    coordinate_path.write_text(json.dumps(changed,ensure_ascii=False,indent=2),encoding='utf-8')
    changed_rejected,changed_error=rejected()
    result=dict(mode=mode,floor=description['floor'],image=description['image'],copiedResourceRoot=str(copied_root.resolve()),
        coordinatePackPath=str(coordinate_path.resolve()),changedPackId=changed['canonicalPackId'],
        python=dict(positiveAccepted=positive.mode==mode,missingInventoryRejected=missing_rejected,missingInventoryError=missing_error,
                    changedPackRejected=changed_rejected,changedPackError=changed_error),
        scope='Only fresh clone manifest membership and clone coordinatePackId changed; original resources and author catalogs untouched')
    (a.output/'fixture.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    if not (result['python']['positiveAccepted'] and missing_rejected and changed_rejected):raise RuntimeError('Original boundary did not reproduce')


if __name__=='__main__':main()
