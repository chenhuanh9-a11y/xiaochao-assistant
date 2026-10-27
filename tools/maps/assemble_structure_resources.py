"""Verify and assemble the structure-only resource Release; no app/setting changes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

REPO=Path(__file__).resolve().parents[2]

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def relative(value):
    p=PurePosixPath(value)
    if not value or p.is_absolute() or '\\' in value or ':' in value or any(x in {'.','..'} for x in p.parts):
        raise ValueError('Unsafe structure path')
    if not (value=='structure-index.json' or value.startswith(('structures/','references/fast-structure/'))):
        raise ValueError('Out-of-scope resource member')
    return p

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets-dir',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True,help='Independent structure data directory.')
    parser.add_argument('--manifest',type=Path,default=REPO/'resources/0B721D65-public1/manifest.json')
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text('utf-8'))
    if manifest['schema']!='xingtu-clean-structure-release-v1':raise ValueError('Not a structure-only manifest')
    if len(manifest['assets'])!=1:raise ValueError('Expected one structure archive')
    asset=manifest['assets'][0];archive=args.assets_dir/asset['name']
    if archive.stat().st_size!=asset['bytes'] or digest(archive)!=asset['sha256']:
        raise ValueError('Archive hash/size mismatch')
    root=args.output_root.resolve();root.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!=set(manifest['files']):raise ValueError('Unexpected archive members')
        for name,row in manifest['files'].items():
            target=root.joinpath(*relative(name).parts)
            if not target.resolve().is_relative_to(root):raise ValueError('Destination escapes root')
            if target.exists() and (not target.is_file() or target.stat().st_size!=row['bytes'] or digest(target)!=row['sha256']):
                raise ValueError('Preserve conflicting local structure: '+name)
        written=0
        for name,row in manifest['files'].items():
            target=root.joinpath(*relative(name).parts)
            if target.exists():continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.structure-',delete=False) as stream:
                temporary=Path(stream.name)
                with z.open(name) as source:shutil.copyfileobj(source,stream)
            if temporary.stat().st_size!=row['bytes'] or digest(temporary)!=row['sha256']:
                temporary.unlink();raise ValueError('Extracted structure hash mismatch: '+name)
            temporary.replace(target);written+=1
    print(json.dumps(dict(success=True,version=manifest['version'],structureFiles=manifest['structureFiles'],
        preparedFiles=manifest['preparedFiles'],resourceFiles=len(manifest['files']),writtenFiles=written,
        outputRoot=str(root),authorImagesIncluded=False,applicationSettingsChanged=False),ensure_ascii=False))

if __name__=='__main__':main()
