"""Measure real Qt resource interactions on a copied, isolated inventory.

The full resource/client path is real; native input uses the existing inert
fixture, window discovery and online startup are disabled by QtClient's probe
mode. Offscreen frames prove submission only, never visible monitor pixels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo, seed, store, output = (p.resolve() for p in
                                (args.repo, args.seed, args.store, args.output))
    if not store.is_relative_to(repo / 'work') or store.exists() or output.exists():
        raise ValueError('Use a NEW store beneath the measured checkout work directory and NEW output')
    if not output.is_relative_to(Path('E:/codex project')):
        raise ValueError('Output must stay in the authorized workspace')
    for p in (seed, *seed.parents, store, *store.parents):
        if p.is_symlink() or (p.exists() and p.stat().st_file_attributes & 0x400):
            raise ValueError('No links/reparse points in benchmark paths')
    store.mkdir(parents=True)
    (store / '.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='ascii')
    shutil.copytree(seed, store / 'work/imported-resources')
    for part in ('app', 'experiments/qt_quick', 'tools', 'tools/maps', 'tests'):
        sys.path.insert(0, str(repo / part))
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['QT_QUICK_BACKEND'] = 'software'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    from PySide6.QtCore import QObject, QPointF, QTimer, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from client import QtClient
    from client_input import NativeShortcutBackend
    from test_client_input import Native
    from benchmark_imported_resource_service import _verify_consumers, _context_archive_paths

    commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    modules = {}
    for name in ('client', 'client_imported_resources', 'client_imported_providers',
                 'imported_resource_archive', 'star_map_download'):
        module = __import__(name)
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(repo), (name, path)
        modules[name] = dict(file=path.relative_to(repo).as_posix(),
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    result = dict(schema='resource-interactions-v1', status='running', sourceCommit=commit,
                  startedAtUtc=datetime.now(timezone.utc).isoformat(), moduleProvenance=modules,
                  machine=dict(os=platform.platform(), python=platform.python_version(),
                               cpu=platform.processor(), logicalCpuCount=os.cpu_count()),
                  conditions=dict(newProcess=True, copiedInventory=True, physicalColdDisk=False,
                                  userCachesCleared=False, nativeInputFixture=True,
                                  gameWindowDiscovery=False, gameStarted=False,
                                  gridBackend='python', offscreen=True, visiblePixelsVerified=False),
                  samples=[], errors=[], warnings=[], closed=False)
    output.parent.mkdir(parents=True, exist_ok=True)

    def write():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    write()
    app = QGuiApplication.instance() or QGuiApplication([])
    client = None
    heartbeat, frames = [0], [0]
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeat.__setitem__(0, heartbeat[0] + 1))
    timer.start()

    def pump(predicate, label, seconds=240):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.002)
        raise TimeoutError(label)

    def idle():
        return (not client.settings.busy and not client.session.busy and not client.resources.busy
                and not client.backend.busy and not client.route_choices.busy
                and not client._initialize_timer.isActive()
                and not client.session.snapshot.get('contextPending')
                and not client.session.snapshot.get('resourceLocked')
                and not client.coordinator._pending_context)

    def ready(season, mode):
        caps = client.session.snapshot.get('savedFrameCapabilities', {}).get(mode, {})
        return (idle() and client.resources.inventoryLoaded and
                (client.session.appliedSeason, client.session.appliedMode) == (season, mode)
                and caps.get('prepared') and caps.get('dataReady')
                and caps.get('season') == season)

    def settle_frame(start_frame, start_heartbeat):
        client.main.update()
        pump(lambda: frames[0] > start_frame and heartbeat[0] > start_heartbeat,
             'offscreen frame and event-loop heartbeat', 30)

    def record(label, start, **values):
        row = dict(operation=label, elapsedMs=(time.perf_counter_ns() - start) / 1e6,
                   endpoint='real-client-state + offscreen-frame-submitted + event-loop-heartbeat',
                   visiblePixelsVerified=False, **values)
        result['samples'].append(row)
        write()
        print(json.dumps(dict(operation=label, elapsedMs=round(row['elapsedMs'], 3))), flush=True)
        return row

    def click(item):
        assert item is not None and item.property('enabled') and item.property('visible'), 'click target unavailable'
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(client.main, Qt.LeftButton, Qt.NoModifier, point)

    try:
        # Do not patch engines, decoders, resource validation, file IO, session,
        # coordinator, settings persistence or QML. Only native input is inert.
        with patch('client_input.NativeShortcutBackend',
                   side_effect=lambda *a, **k: NativeShortcutBackend(Native())):
            start = time.perf_counter_ns()
            client = QtClient(app, store, probe_output=store / 'unused-probe.json',
                              info_fetch=lambda *a, **k: {}, grid_backend='python')
            client.main.frameSwapped.connect(lambda: frames.__setitem__(0, frames[0] + 1))
            client.resources.errorReported.connect(lambda event: result['errors'].append(
                dict(code=event.get('errorCode'), action=event.get('action'))))
            client.main.show()
            pump(lambda: ready('s0', 'nightmare'), 'first full client preparation')
            proof = _verify_consumers(client.resource_resolver, client.providers, 's0', 'nightmare')
            selector = client.main.findChild(QObject, 'homeGameContext')
            assert selector is not None
            pump(lambda: selector.property('enabled'), 'home mode selector enabled')
            settle_frame(frames[0], heartbeat[0])
            record('first-client-load', start, consumer=proof, constructorIncluded=True)

            # Real QML difficulty button clicks; season uses keyboard selection
            # in the real ComboBox. No direct provider.prepare_context shortcut.
            sequence = [('s0', 'difficult'), ('s1', 'difficult'), ('s1', 'nightmare'),
                        ('s0', 'nightmare')] * 2
            for index, (season, mode) in enumerate(sequence):
                current = (client.session.appliedSeason, client.session.appliedMode)
                pump(idle, 'idle before switch')
                start = time.perf_counter_ns()
                if current[0] != season:
                    combo = client.main.findChild(QObject, 'homeGameContextSeason')
                    click(combo)
                    QTest.keyClick(client.main, Qt.Key_Up if season == 's0' else Qt.Key_Down)
                    QTest.keyClick(client.main, Qt.Key_Return)
                    entry = 'QTest mouse + ComboBox keyboard'
                else:
                    button = client.main.findChild(QObject, 'homeGameContext' +
                                                  ('Difficult' if mode == 'difficult' else 'Nightmare'))
                    click(button)
                    entry = 'QTest mouse on real QML difficulty button'
                pump(lambda: ready(season, mode), 'clicked context ready')
                proof = _verify_consumers(client.resource_resolver, client.providers, season, mode)
                pump(lambda: selector.property('enabled'), 'mode selector enabled again')
                settle_frame(frames[0], heartbeat[0])
                record('switch-context', start, source=list(current), target=[season, mode],
                       passIndex=index // 4 + 1, entrypoint=entry, consumer=proof)

            archives = _context_archive_paths(args.source)
            for kind in ('route', 'base'):
                source = next(row for row in archives if row['season'] == 's0'
                              and row['mode'] == 'nightmare' and row['kind'] == kind)
                archive = source['path']
                import zipfile
                with zipfile.ZipFile(archive) as z:
                    from imported_resource_packages import MANIFEST_NAME
                    package_id = json.loads(z.read(MANIFEST_NAME))['packageId']
                for repetition in (1, 2):
                    pump(idle, 'idle before removal')
                    row = next(row for row in client.resources.packageRows if row['packageId'] == package_id)
                    before_count = len(client.resources.packageRows)
                    start = time.perf_counter_ns()
                    assert client.resources.requestRemoval(row['key'])
                    pump(lambda: idle() and client.resources.removalPlanOpen,
                         'real deletion confirmation ready')
                    inspect_ms = (time.perf_counter_ns() - start) / 1e6
                    plan_id = client.resources.removalPlanId
                    confirm = time.perf_counter_ns()
                    assert client.resources.confirmRemoval(plan_id), 'confirmation rejected: ' + client.resources.message
                    pump(lambda: idle() and len(client.resources.packageRows) == before_count - 1,
                         'deletion, final inventory and client preparation')
                    settle_frame(frames[0], heartbeat[0])
                    record('remove-' + kind, start, repetition=repetition,
                           inspectMs=inspect_ms,
                           confirmThroughReadyMs=(time.perf_counter_ns() - confirm) / 1e6,
                           entrypoint='ImportedResourceService.requestRemoval + confirmRemoval; no human dwell',
                           remainingPackages=len(client.resources.packageRows),
                           packageId=package_id, packageKey=row['key'],
                           recoveryRequired=bool(client.resources.recoveryRequired))
                    assert not client.resources.recoveryRequired
                    # Restore this SAME package with the real importer, outside
                    # the measured interval. No user/original resource is edited.
                    from PySide6.QtCore import QUrl
                    done = []
                    def completed(*_):
                        done.append(True)
                    client.resources.importCompleted.connect(completed)
                    assert client.resources.importArchive(QUrl.fromLocalFile(str(archive)).toString(), 's0')
                    pump(lambda: bool(done) and idle() and not client.resources.activationPlanOpen,
                         'untimed same-package restore')
                    client.resources.importCompleted.disconnect(completed)
                    assert len(client.resources.packageRows) == before_count
            result['status'] = 'passed'
    except Exception as error:
        result['status'] = 'failed'
        result['failure'] = dict(type=type(error).__name__, message=str(error), traceback=traceback.format_exc())
        if client is not None:
            result['failure']['snapshot'] = client.session.snapshot
    finally:
        timer.stop()
        if client is not None:
            result['warnings'] = list(client.warnings)
            try:
                client.shutdown()
                result['closed'] = bool(client._shutdown_complete)
            except Exception as error:
                result['shutdownFailure'] = str(error)
                result['status'] = 'failed'
        result['finishedAtUtc'] = datetime.now(timezone.utc).isoformat()
        write()
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
