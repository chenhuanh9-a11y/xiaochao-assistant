"""Generate optional fast indices from the exact packaged shared references."""
import argparse
from dataclasses import replace
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'app'))
from fast_structure_alignment import prepare, load_prepared
from shared_structure_reference import load_shared_structure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resource_root', type=Path)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    root = args.resource_root.resolve()
    output_root = args.output_root.resolve()
    if output_root == root or output_root.is_relative_to(root):
        parser.error('Use an independent output root; source resources remain read-only.')
    index = json.loads((root/'references/nightmare-shared-structure-index.json').read_text('utf8'))
    rows = []
    for entry in index['entries']:
        path = (root / entry['resourcePath']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Shared reference escapes the requested resource root')
        output = output_root/'references/fast-structure'/(path.stem+'-fast.npz')
        reference = load_shared_structure(path)
        if reference is None:
            raise ValueError(f'Invalid shared reference: {path}')
        # Relocate only the path for independent frozen-loader readback.
        output_reference = replace(reference, path=output_root/'references'/'shared-structure'/path.name)
        if output.exists():
            if load_prepared(output_reference) is not None:
                continue
            raise ValueError(f'Preserve incompatible existing output: {output}')
        prepared = prepare(reference.clean_mask)
        output.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(output, distance=prepared.distance, chains=prepared.chains,
            rays=prepared.rays, pitch=prepared.pitch, shape=prepared.shape,
            sourceMetadata=json.dumps(reference.metadata, sort_keys=True))
        if load_prepared(output_reference) is None:
            raise ValueError(f'Frozen runtime loader rejected prepared output: {output}')
        rows.append(dict(map=entry['mapId'],floor=entry['floor'],bytes=output.stat().st_size))
    print(json.dumps(dict(generated=len(rows),bytes=sum(r['bytes'] for r in rows),rows=rows)))


if __name__ == '__main__':
    main()
