"""Bounded map maintenance: validated inputs, diff, content-addressed staging.

Standard library only. Never invokes extractors, solvers, application or promotion.
Exit: 0 = requested inputs/plan operation complete, 2 = invalid, 3 = release blocked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
ROLES = ('clean', 'geometry', 'recognition', 'rendered_display')
BLOCKED = (
    'blocked adapter: native-package-to-normalized-manifest and incremental extractor',
    'blocked shared-core gate: registration inventory and representative affected-chain recognition checks',
    'blocked author adapter: base/alignment/crop/alpha/coordinate validation',
    'blocked runtime adapter: clean package loading and integration status',
    'blocked adapter: installable delta packaging, rollback and promotion',
)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sha(value):
    need(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value),
         'expected lowercase SHA256')
    return value


def text_field(obj, field):
    need(isinstance(obj.get(field), str) and bool(obj[field].strip()),
         f'missing/non-string {field}')
    return obj[field]


def key(row):
    return tuple(row[f] for f in ('layout_id', 'map_guid', 'floor'))


def base_key(row):
    return (*key(row), row['base_structure_version'], row['structure_hash'])


def all_rows(doc):
    return doc['floors'] + doc.get('retained_bases', [])


def path_for(root, asset):
    path = Path(asset['path'])
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def all_assets(doc):
    return ([r['source'] for r in all_rows(doc)] +
            [a for r in all_rows(doc) for a in r.get('artifacts', {}).values()] +
            [a['asset'] for r in all_rows(doc) for a in r.get('author_variants', [])])


def validate_asset(root, asset):
    need(isinstance(asset, dict), 'asset must be an object')
    text_field(asset, 'path')
    sha(asset.get('sha256'))
    path = path_for(root, asset)
    need(path.is_file(), f'missing package asset: {path}')
    need(file_hash(path) == asset['sha256'], f'SHA256 mismatch: {path}')


def load_manifest(path):
    path = Path(path).resolve()
    doc = json.loads(path.read_text(encoding='utf-8-sig'))
    need(isinstance(doc, dict) and type(doc.get('schema')) is int and doc['schema'] == 2,
         'unsupported manifest schema')
    for field in ('version', 'mode'):
        text_field(doc, field)
    need('base_version' in doc and (doc['base_version'] is None or
         isinstance(doc['base_version'], str) and doc['base_version'].strip()), 'invalid base_version')
    need(isinstance(doc.get('tools'), dict), 'missing tools')
    for field in ('extractor', 'opencv'):
        text_field(doc['tools'], field)
    need(isinstance(doc.get('floors'), list) and doc['floors'], 'empty/missing target floors')
    need(isinstance(doc.get('retained_bases', []), list), 'retained_bases must be a list')
    seen, seen_bases, layouts, guids = set(), set(), {}, {}
    canonical_displays = set()
    for i, row in enumerate(all_rows(doc)):
        current = i < len(doc['floors'])
        need(isinstance(row, dict), 'floor must be an object')
        for field in ('layout_id', 'map_guid', 'floor', 'source_version',
                      'base_structure_version', 'coordinate_system'):
            text_field(row, field)
        k = key(row)
        if current:
            need(k not in seen, f'duplicate target floor: {k}')
            seen.add(k)
        bk = base_key(row)
        need(bk not in seen_bases, f'duplicate base revision: {bk}')
        seen_bases.add(bk)
        need(layouts.setdefault(k[0], k[1]) == k[1], f'conflicting layout/GUID: {k}')
        need(guids.setdefault(k[1], k[0]) == k[0], f'conflicting GUID/layout: {k}')
        if current:
            need(row['source_version'] == doc['version'], f'wrong source_version: {k}')
        else:
            need(isinstance(row.get('tools'), dict), 'retained base must preserve producer tools')
        if 'tools' in row:
            need(isinstance(row['tools'], dict), 'row tools must be an object')
            for field in ('extractor', 'opencv'):
                text_field(row['tools'], field)
        sha(row.get('structure_hash'))
        sha(row.get('display_hash'))
        validate_asset(path.parent, row.get('source'))
        artifacts = row.get('artifacts', {})
        need(isinstance(artifacts, dict) and set(artifacts) <= set(ROLES), 'unknown artifact role')
        for role, asset in artifacts.items():
            validate_asset(path.parent, asset)
            need(asset.get('structure_hash') == row['structure_hash'], f'wrong artifact base: {k}/{role}')
            if role == 'rendered_display':
                need(asset.get('kind') == 'game-rendered', 'default display must be game-rendered')
        authors = row.get('author_variants', [])
        need(isinstance(authors, list), 'author_variants must be a list')
        author_ids = set()
        for author in authors:
            required = {'id', 'base_version', 'base_key', 'baked', 'transform_applications', 'asset'}
            need(isinstance(author, dict) and required <= set(author) <= required | {'author_id'},
                'author must reference shared base; unknown/embedded base fields forbidden')
            aid = text_field(author, 'id')
            need(aid not in author_ids, f'duplicate author variant: {aid}')
            author_ids.add(aid)
            need(author['base_version'] == row['base_structure_version'] and author['base_key'] == list(bk),
                 f'wrong author base version/key: {aid}')
            author_id = text_field(author, 'author_id') if 'author_id' in author else aid
            canonical_key = (author_id, *bk)
            need(canonical_key not in canonical_displays,
                 f'multiple canonical displays for author/base: {author_id}/{bk}')
            canonical_displays.add(canonical_key)
            need(type(author['baked']) is bool and type(author['transform_applications']) is int,
                 'invalid transform declaration')
            need(author['transform_applications'] == (0 if author['baked'] else 1),
                 f'duplicate/missing author transform: {aid}')
            validate_asset(path.parent, author['asset'])
    return doc, path.parent


def cache_key(doc, row):
    return digest({'schema': doc['schema'], 'source': row['source']['sha256'],
                   'structure': row['structure_hash'], 'tools': row.get('tools', doc['tools']),
                   'base_structure_version': row['base_structure_version'],
                   'coordinate_system': row['coordinate_system'], 'key': key(row)})


def coverage(doc):
    missing = [{'key': list(key(row)), 'roles': [r for r in ROLES if r not in row.get('artifacts', {})]}
               for row in doc['floors']]
    missing = [r for r in missing if r['roles']]
    return {'target_maps': len({r['map_guid'] for r in doc['floors']}),
            'target_floors': len(doc['floors']),
            'prepared_floors': len(doc['floors']) - len(missing), 'missing_products': missing,
            'retained_base_count': len(doc.get('retained_bases', [])),
            'retained_missing_products': [
                {'base_key': list(base_key(r)), 'roles': [role for role in ROLES if role not in r.get('artifacts', {})]}
                for r in doc.get('retained_bases', [])
                if not all(role in r.get('artifacts', {}) for role in ROLES)],
            'base_versions': [{'logical_key': list(key(r)), 'base_key': list(base_key(r)),
                               'scope': 'current' if i < len(doc['floors']) else 'retained',
                               'prepared': all(role in r.get('artifacts', {}) for role in ROLES)}
                              for i, r in enumerate(all_rows(doc))]}


def plan(old, new):
    need(new['base_version'] == (old['version'] if old else None), 'wrong/missing baseline version')
    if old:
        need(old['mode'] == new['mode'], 'mode mismatch; use separate mode plans')
    before = {key(r): r for r in old['floors']} if old else {}
    after = {key(r): r for r in new['floors']}
    changes = {name: [] for name in ('added', 'structure_changed', 'display_changed',
                                    'author_variants_changed', 'source_changed',
                                    'deletion_candidates', 'cache_invalidated')}
    for k, row in after.items():
        prior = before.get(k)
        if prior is None:
            changes['added'].append(list(k))
        else:
            for name, fields in (
                ('structure_changed', ('structure_hash', 'base_structure_version', 'coordinate_system')),
                ('display_changed', ('display_hash',)),
                ('author_variants_changed', ('author_variants',)),
                ('source_changed', ('source', 'source_version')),
            ):
                if any(prior.get(f, [] if f == 'author_variants' else None) !=
                       row.get(f, [] if f == 'author_variants' else None) for f in fields):
                    changes[name].append(list(k))
        if prior is None or cache_key(old, prior) != cache_key(new, row):
            changes['cache_invalidated'].append(list(k))
    changes['deletion_candidates'] = [list(k) for k in before.keys() - after.keys()]
    bindings = [{'old': list(a), 'new': list(b)} for a in before for b in after
                if a != b and a[2] == b[2] and (a[0] == b[0] or a[1] == b[1])]
    retained = {base_key(r) for r in new.get('retained_bases', [])}
    invalidated = [{'logical_key': list(k), 'old_base': list(base_key(before[k])),
                    'new_base': list(base_key(after[k])),
                    'prior_author_ids': [a['id'] for a in before[k].get('author_variants', [])],
                    'old_base_retained': base_key(before[k]) in retained,
                    'required_action': 'keep old author on old base or independently validate migration'}
                   for k in sorted(before.keys() & after.keys())
                   if base_key(before[k]) != base_key(after[k])]
    return {'status': 'PLANNED', 'schema': 2, 'version': new['version'],
            'base_version': new['base_version'], 'manifest_fingerprint': digest(new),
            'changes': {k: sorted(v) for k, v in changes.items()}, 'binding_changes': bindings,
            'invalidated_mappings': invalidated,
            'coverage': coverage(new), 'blocked': list(BLOCKED), 'release_ready': False}


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.map-update-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(obj, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def stage(doc, root, out):
    out = Path(out).resolve()
    # Never stage over input files/directories, including via a junction/symlink.
    assets = all_assets(doc)
    need(not any(path_for(root, a).is_relative_to(out) for a in assets),
         'staging must not contain source/prepared assets')
    marker = out / '.map-update-run.json'
    identity = {'manifest_fingerprint': digest(doc), 'input_root': str(root)}
    if out.exists():
        need(marker.is_file(), 'refuse existing unowned staging directory')
        need(json.loads(marker.read_text(encoding='utf-8')) == identity,
             'staging belongs to another input/version; choose a fresh directory')
    else:
        out.mkdir(parents=True)
        write_json(marker, identity)
    resources = out / 'resources'
    need(not resources.is_symlink() and resources.resolve() == resources,
         'refuse linked resource directory')
    resources.mkdir(exist_ok=True)
    entries, retained_entries, copied = [], [], set()

    def collect(asset):
        target = resources / asset['sha256']
        need(not target.is_symlink(), 'refuse linked staged resource')
        if target.exists():
            need(file_hash(target) == asset['sha256'], f'corrupt staged resource: {target}')
        else:
            fd, tmp = tempfile.mkstemp(prefix='.copy-', dir=resources)
            try:
                with os.fdopen(fd, 'wb') as dest, path_for(root, asset).open('rb') as src:
                    for block in iter(lambda: src.read(1024 * 1024), b''):
                        dest.write(block)
                need(file_hash(tmp) == asset['sha256'], 'input changed during copy')
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        copied.add(target.name)
        return 'resources/' + target.name

    for i, row in enumerate(all_rows(doc)):
        if not all(role in row.get('artifacts', {}) for role in ROLES):
            continue
        products = {role: collect(row['artifacts'][role]) for role in ROLES}
        authors = [{**{k: v for k, v in a.items() if k != 'asset'}, 'asset': collect(a['asset'])}
                   for a in row.get('author_variants', [])]
        entry = {'key': list(key(row)), 'base_key': list(base_key(row)),
                        'base_version': row['base_structure_version'],
                        'structure_hash': row['structure_hash'], 'cache_key': cache_key(doc, row),
                        'products': products, 'default_display': products['rendered_display'],
                        'author_variants': authors}
        (entries if i < len(doc['floors']) else retained_entries).append(entry)
    index = {'schema': 2, 'version': doc['version'], 'entries': entries,
             'retained_bases': retained_entries,
             'runtime_schema': False, 'coverage': coverage(doc)}
    write_json(out / 'index.json', index)
    result = {'status': 'STAGED_BLOCKED', **identity, 'version': doc['version'],
              'base_version': doc['base_version'], 'coverage': coverage(doc),
              'staged_floors': len(entries), 'unique_resources': len(copied),
              'staged_retained_bases': len(retained_entries),
              'resource_bytes': sum((resources / p).stat().st_size for p in copied),
              'blocked': list(BLOCKED), 'release_ready': False,
              'author_resource_validation': 'not_evaluated_by_this_collector',
              'runtime_integration': 'blocked_adapter',
              'evidence': {'inputs': 'schema/hash/declaration checks only',
                           'synthetic': 'not supplied', 'real_replay': 'not supplied',
                           'visual': 'not supplied'},
              'rollback': 'Production untouched; retain base_version and original manifests.'}
    write_json(out / 'run.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    check = sub.add_parser('check')
    check.add_argument('--manifest', required=True)
    check.add_argument('--scope', choices=('inputs', 'release'), required=True)
    diff = sub.add_parser('plan')
    diff.add_argument('--old')
    diff.add_argument('--new', required=True)
    diff.add_argument('--out', required=True)
    build = sub.add_parser('stage')
    build.add_argument('--manifest', required=True)
    build.add_argument('--old', help='required baseline manifest for incremental staging')
    build.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'plan':
            old, old_root = load_manifest(args.old) if args.old else (None, None)
            new, root = load_manifest(args.new)
            result = plan(old, new)
            # Do not overwrite source manifests, even on a mistyped --out.
            protected = [Path(p).resolve() for p in (args.old, args.new) if p]
            protected += [path_for(root, a) for a in all_assets(new)]
            if old:
                protected += [path_for(old_root, a) for a in all_assets(old)]
            need(Path(args.out).resolve() not in protected,
                 'plan output must differ from input manifests/assets')
            write_json(args.out, result)
            code = 0
        else:
            doc, root = load_manifest(args.manifest)
            if args.cmd == 'stage':
                old = load_manifest(args.old)[0] if args.old else None
                plan(old, doc)  # reject unbound/wrong incremental baselines before copying
                result, code = stage(doc, root, args.out), 3
            elif args.scope == 'release':
                result, code = {'status': 'BLOCKED', 'coverage': coverage(doc),
                                'blocked': list(BLOCKED), 'release_ready': False}, 3
            else:
                result, code = {'status': 'INPUTS_VALID_ONLY', 'coverage': coverage(doc),
                                'release_ready': False}, 0
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return code
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(json.dumps({'status': 'INVALID', 'error': str(exc), 'release_ready': False},
                         ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
