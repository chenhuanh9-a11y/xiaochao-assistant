"""CI-only frozen parent. Not the Xingtu client, UI, installer, or game runtime."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    if not getattr(sys, 'frozen', False) or os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise ValueError('Synthetic hosted-runner parent only')
    root = Path(sys.executable).resolve().parent
    if not root.is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('Parent escaped hosted-runner temporary root')
    profile = json.loads((Path(sys._MEIPASS) / 'installation-profile.json').read_text('utf-8'))
    marker = json.loads((root / 'synthetic-probe.json').read_text('utf-8'))
    if profile.get('syntheticHelperBusinessProbe') is not True or marker['probeId'] != profile.get('probeId'):
        raise ValueError('Not the baked synthetic probe')
    archive = root / 'fixture-author.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != marker['archiveSha256']:
        raise ValueError('Synthetic archive changed')
    from star_map_import_helper import launch_import_helper, application_process_identity
    from resource_transaction import resource_runtime_lease
    with resource_runtime_lease(root):
        helper = launch_import_helper(root / 'star-map-import-helper.exe', archive, root,
            marker['archiveSha256'], marker['requestId'], wait_seconds=60)
        record = dict(schema='xingtu-synthetic-parent-handoff-v1', probeId=marker['probeId'],
            parent=application_process_identity(), child=application_process_identity(helper.pid),
            requestId=marker['requestId'], archiveSha256=marker['archiveSha256'], realRuntimeLeaseHeld=True)
        pending = root / 'handoff.tmp'
        with pending.open('x', encoding='utf-8') as stream:
            json.dump(record, stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(pending, root / 'handoff.json')
        # Retain a verified real child handle before the parent normally exits.
        # Do NOT wait for helper completion: it is waiting for this parent.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready = root / 'observer-ready.json'
            if ready.exists():
                value = json.loads(ready.read_text('utf-8'))
                if value != dict(probeId=marker['probeId'], child=record['child']):
                    raise ValueError('Observer handshake identity mismatch')
                return 0
            time.sleep(.05)
        raise TimeoutError('Observer did not retain the real helper process')


if __name__ == '__main__':
    raise SystemExit(main())
