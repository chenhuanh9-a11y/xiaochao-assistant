"""Read-only build gate for declared runtime paths, preserving producer provenance.

Invoke against the authorized staged _internal directory before freezing a build.
This checks path ownership, not image availability or full runtime acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath, PureWindowsPath


def check_relative_path(root, value):
    """Reject absolute/drive-relative names and paths escaping the package root."""
    if not isinstance(value, str) or not value.strip():
        return 'runtime path must be a nonempty string'
    windows = PureWindowsPath(value)
    if windows.drive or windows.root or PurePosixPath(value).is_absolute():
        return 'runtime path must be package-relative'
    root = Path(root).resolve()
    path = (root / value.replace('\\', '/')).resolve()
    if not path.is_relative_to(root):
        return 'runtime path escapes package root'
    return None


def check_package(root):
    root = Path(root).resolve()
    errors, checked = [], []

    def check(file, field, value):
        checked.append(dict(file=file, field=field))
        if reason := check_relative_path(root, value):
            errors.append(dict(file=file, field=field, reason=reason))

    def read(file):
        try:
            return json.loads((root/file).read_text('utf-8-sig'))
        except (OSError, ValueError) as error:
            errors.append(dict(file=file, field='', reason=str(error)))
            return {}

    name = 'references/nightmare-map-pack.json'
    nightmare = read(name)
    for i, row in enumerate(nightmare.get('maps', [])):
        base = row.get('resourceBase', 'source-root')
        if base not in {'resource-root', 'source-root'}:
            errors.append(dict(file=name, field=f'maps/{i}/resourceBase', reason='unsupported resource base'))
        for key, value in [('oneFlow', row.get('oneFlow')), *[(f'layers/{k}', v) for k, v in row.get('layers', {}).items()]]:
            check(name, f'maps/{i}/{key}', value)
    # sourceRoot is intentionally NOT a path input in frozen runtime: it is
    # provenance for resource-root rows and ignored as a legacy fallback.
    name = 'difficult-native-manifest.json'
    interfaces = read(name).get('runtimeInterfaces', {})
    for key, value in interfaces.items():
        if key != 'legacyClean':
            check(name, 'runtimeInterfaces/'+key, value)
    legacy = interfaces.get('legacyClean') or {}
    for key in ('index', 'resourceRoot', 'entries', 'sideEntries', 'passports', 'coordinateManifest'):
        if key in legacy:
            check(name, 'runtimeInterfaces/legacyClean/'+key, legacy[key])
    for floor, row in legacy.get('referenceCanvas', {}).items():
        check(name, f'runtimeInterfaces/legacyClean/referenceCanvas/{floor}/resourcePath', row.get('resourcePath'))
    for name in ('references/nightmare-shared-structure-index.json',
                 'references/difficult-shared-structure-index.json'):
        index = read(name)
        check(name, 'structureVersionBindings', index.get('structureVersionBindings'))
        for i, row in enumerate(index.get('entries', [])):
            check(name, f'entries/{i}/resourcePath', row.get('resourcePath'))
    return dict(success=not errors, checkedRuntimePaths=len(checked), errors=errors,
        scope='Nightmare map routes, native runtime interfaces/legacy references, and shared typed-product indices. '
              'Producer provenance remains unchanged; this does not certify runtime resource completeness.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resource_root', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = check_package(args.resource_root)
    text = json.dumps(report, ensure_ascii=False, indent=2)+'\n'
    if args.report:
        args.report.write_text(text, encoding='utf8')
    print(text)
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
