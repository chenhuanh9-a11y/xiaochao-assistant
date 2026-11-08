"""Retention policies on temp fixtures; native delete IO is explicitly substituted."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import diagnostic_storage as logs
import failure_sampling as images
from nightmare_candidate import NightmareMemoryStore
from retention_io_fixture import image_io, retire_log

@pytest.fixture
def log_io():
    with patch.object(logs, '_retire_log', retire_log, create=True), patch.object(logs, '_known_roots', set(), create=True):
        yield


def test_seven_days_expires_only_original_managed_logs(tmp_path, log_io):
    old = tmp_path / 'events-old.jsonl'
    changed = tmp_path / 'events-changed.jsonl'
    for path in (old, changed): logs.append_record(path, {'event':'capture-error'}, now=0)
    changed.write_text('user replacement')
    protected = ['events-unknown.jsonl','export.zip','confirmations.jsonl','alignment-acceptances.jsonl','samples.png']
    for name in protected: (tmp_path / name).write_text('keep')
    logs.append_record(tmp_path/'events-new.jsonl', {'event':'capture-error'}, now=7*86400)
    assert not old.exists()
    assert changed.read_text() == 'user replacement'
    for name in protected: assert (tmp_path/name).read_text() == 'keep'


def test_capacity_retires_oldest_managed_log(tmp_path, log_io):
    for i in range(3):
        logs.append_record(tmp_path/f'events-{i}.jsonl', {'event':'capture-error'}, now=i, max_files=2)
    assert not (tmp_path/'events-0.jsonl').exists()
    assert (tmp_path/'events-1.jsonl').exists()
    assert (tmp_path/'events-2.jsonl').exists()


def test_changed_identity_even_same_bytes_is_not_owned(tmp_path, log_io):
    path = tmp_path/'events-old.jsonl'
    logs.append_record(path, {'event':'capture-error'}, now=0)
    path.rename(tmp_path/'saved-original')
    path.write_bytes((tmp_path/'saved-original').read_bytes())
    logs.append_record(tmp_path/'events-new.jsonl', {'event':'capture-error'}, now=8*86400)
    assert path.exists()


def test_managed_log_roots_share_file_budget(tmp_path, log_io):
    with patch.object(logs,'ROOT_FILES',1):
        logs.append_record(tmp_path/'one'/'events-a.jsonl', {'event':'capture-error'})
        with pytest.raises(OSError):
            logs.append_record(tmp_path/'two'/'events-b.jsonl', {'event':'capture-error'})
    assert (tmp_path/'one'/'events-a.jsonl').exists()
    assert not (tmp_path/'two'/'events-b.jsonl').exists()


def test_legacy_recursive_cleanup_cannot_remove_unknown_or_export(tmp_path):
    store = NightmareMemoryStore(SimpleNamespace(by_id={}, mode='nightmare'), tmp_path, portable_seed_path=None)
    protected = ['failures/unknown.png', 'failures/export.zip', 'failures/automatic/old.png',
                 'confirmations.jsonl','alignment-acceptances.jsonl','alignment-rejections.jsonl','samples/learn.png']
    for name in protected:
        p=tmp_path/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'keep')
    store._failure_sampling.max_disk_bytes=1
    with pytest.raises(OSError): store._reserve_failure_bytes(1)
    assert all((tmp_path/name).read_bytes()==b'keep' for name in protected)
    assert store._failure_sampling.last_status == 'full-frame-ownership-unavailable'


def test_full_frame_budget_is_64_mib():
    assert images.FailureSampling.max_disk_bytes == 64*1024*1024


def test_full_frame_seven_days_and_journal_reason(tmp_path):
    with image_io():
        row=images.save_full_failure_frame(tmp_path, b'png', {'recordedAt':'2020-01-01T00:00:00+00:00'})
        report=images.cleanup_automatic_failure_samples(tmp_path, now=1577836800+7*86400, namespace='full-v2')
        assert report['complete'] and report['removed']==1
        assert not (tmp_path/row['samplePath']).exists()
        journal=images.automatic_storage_inventory(tmp_path, namespace='full-v2')
        assert journal['rows'][0][1]['missingReason']=='automatic-image-retired-expired'


def test_full_and_local_images_share_capacity(tmp_path):
    with image_io(), patch.object(images.FailureSampling, 'max_disk_bytes', 8):
        # Seed a separate mode's local crop using production create + inventory.
        other=tmp_path/'difficult'
        with images.automatic_storage_transaction(other, create=True, namespace='automatic-v2'):
            relative='failures/automatic-v2/auto-'+'1'*32+'.png'
            row=dict(event='automatic-failure-sample',recordedAt='2099-11-08T00:00:00+00:00',samplePath=relative)
            row.update(images.create_automatic_image(other, relative, b'crop'))
            images.replace_automatic_journal(images._directory(other,'automatic-v2'), (json.dumps(row)+'\n').encode())
        full=tmp_path/'nightmare'
        first=images.save_full_failure_frame(full,b'full',{})
        second=images.save_full_failure_frame(full,b'next',{})
        assert not (full/first['samplePath']).exists()
        assert (full/second['samplePath']).exists()
        assert (other/relative).exists()
        report=images.automatic_storage_inventory(full,namespace='full-v2')
        assert report['rows'][0][1]['missingReason']=='automatic-image-retired-capacity'


def test_full_frame_unknown_directory_stops_new_writes(tmp_path):
    unknown=tmp_path/'failures/full-v2/export.zip'
    unknown.parent.mkdir(parents=True); unknown.write_bytes(b'ZIP')
    with image_io(), pytest.raises(OSError): images.save_full_failure_frame(tmp_path,b'png',{})
    assert unknown.read_bytes()==b'ZIP'
    assert not list(unknown.parent.glob('*.png'))


def test_log_append_does_not_extend_old_records_past_seven_days(tmp_path, log_io):
    path=tmp_path/'events-stream.jsonl'
    logs.append_record(path, {'event':'capture-error','seq':1}, now=0)
    logs.append_record(path, {'event':'capture-error','seq':2}, now=6*86400)
    logs.append_record(path, {'event':'capture-error','seq':3}, now=7*86400)
    assert [json.loads(line)['seq'] for line in path.read_bytes().splitlines()] == [3]


def test_global_capacity_retires_oldest_other_context(tmp_path):
    with image_io(), patch.object(images.FailureSampling,'max_disk_bytes',8):
        old=images.save_full_failure_frame(tmp_path/'nightmare',b'old!',{})
        new=images.save_full_failure_frame(tmp_path/'difficult',b'new!',{})
        last=images.save_full_failure_frame(tmp_path/'difficult',b'last',{})
        assert not (tmp_path/'nightmare'/old['samplePath']).exists()
        assert (tmp_path/'difficult'/new['samplePath']).exists()
        assert (tmp_path/'difficult'/last['samplePath']).exists()


def test_full_frames_default_off_and_explicit_disabled_settings_stay_off(tmp_path):
    from client_failure_samples import FailureSampleRecorder
    from client_settings import SettingsService
    assert FailureSampleRecorder().enabled is False
    config=tmp_path/'ui-settings.json'
    config.write_text(json.dumps({'automaticFailureEvidence':False}))
    from client_paths import RuntimePaths
    with patch('client_settings.runtime_paths', return_value=RuntimePaths(tmp_path,False)):
        settings=SettingsService(tmp_path)
    assert settings.persistedValues['automaticFailureEvidence'] is False
    assert FailureSampleRecorder().enabled is False


def test_export_destination_rejects_internal_cache(tmp_path):
    from diagnostic_export import export_safe_diagnostic_bundle
    for target in (tmp_path/'live/inside.zip',tmp_path/'memory/failures/inside.zip'):
        with pytest.raises(ValueError, match='cache'):
            export_safe_diagnostic_bundle(tmp_path,tmp_path/'live',
                app_version='test',build_fingerprint='TEST',failure_roots=(tmp_path/'memory',),destination=target)
        assert not target.exists()


def test_full_frame_disk_metadata_excludes_window_titles(tmp_path):
    import numpy as np
    store=NightmareMemoryStore(SimpleNamespace(by_id={},mode='nightmare'),tmp_path,portable_seed_path=None)
    with image_io():
        row=store.record_alignment_failure(None,'1','identity-unresolved',{},np.zeros((3,3,3),dtype=np.uint8),
            diagnostic={'stage':'identity-rejection','mode':'nightmare','cycleId':1,'windowTitle':'PRIVATE-WINDOW',
                        'otherProgram':'PRIVATE-PROGRAM'})
        data=(tmp_path/'failures/full-v2/automatic-failures.jsonl').read_bytes()
        assert b'PRIVATE' not in data
        assert row['fullFrame'] is True


def test_caller_cannot_extend_retention_beyond_seven_days(tmp_path, log_io):
    path=tmp_path/'events-old.jsonl'
    logs.append_record(path,{'event':'capture-error'},now=0,max_age=10*86400)
    logs.append_record(tmp_path/'events-new.jsonl',{'event':'capture-error'},now=8*86400,max_age=10*86400)
    assert not path.exists()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='requires actual Windows exclusive handles')
def test_native_full_frame_expiry_and_replacement_protection(tmp_path):
    row=images.save_full_failure_frame(tmp_path,b'png',{'recordedAt':'2020-01-01T00:00:00+00:00'})
    path=tmp_path/row['samplePath']
    path.rename(tmp_path/'original')
    path.write_bytes(b'png')
    report=images.cleanup_automatic_failure_samples(tmp_path,now=1577836800+8*86400,namespace='full-v2')
    assert not report['complete']
    assert path.read_bytes()==b'png'


@pytest.mark.skipif(__import__('os').name != 'nt', reason='requires actual Windows exclusive handles')
def test_native_log_expiry(tmp_path):
    path=tmp_path/'events-old.jsonl'
    logs.append_record(path,{'event':'capture-error'},now=0)
    logs.append_record(tmp_path/'events-new.jsonl',{'event':'capture-error'},now=7*86400)
    assert not path.exists()


@pytest.mark.skipif(__import__('os').name != 'nt', reason='requires actual Windows exclusive handles')
def test_native_full_frame_expires_original_by_handle(tmp_path):
    row=images.save_full_failure_frame(tmp_path,b'png',{'recordedAt':'2020-01-01T00:00:00+00:00'})
    report=images.cleanup_automatic_failure_samples(tmp_path,now=1577836800+7*86400,namespace='full-v2')
    assert report['complete'] and report['removed']==1
    assert not (tmp_path/row['samplePath']).exists()


@pytest.mark.parametrize('trigger', ['registration', 'periodic'])
@pytest.mark.parametrize('namespace', ['automatic-v2', 'full-v2'])
def test_idle_registered_images_expire(tmp_path, trigger, namespace):
    with image_io(), patch.object(images, '_start_retention_worker', create=True):
        idle = tmp_path/'s0-difficult'
        with images.automatic_storage_transaction(idle, create=True, namespace=namespace):
            relative = f'failures/{namespace}/auto-'+'2'*32+'.png'
            row = dict(event='automatic-failure-sample', recordedAt='2020-01-01T00:00:00+00:00', samplePath=relative)
            row.update(images.create_automatic_image(idle, relative, b'crop', namespace=namespace))
            images.replace_automatic_journal(images._directory(idle, namespace), (json.dumps(row)+'\n').encode())
        # A changed original must never be retired. Keep unknown evidence outside
        # the managed namespace so it does not deliberately fail its inventory.
        unknown = idle/'failures/user.png'
        unknown.write_bytes(b'keep')
        if trigger == 'registration':
            images.register_failure_root(tmp_path/'s1-nightmare')
        else:
            images.prune_registered_failure_roots(now=1577836800+8*86400)
        assert not (idle/relative).exists()
        assert unknown.read_bytes() == b'keep'


@pytest.mark.parametrize('files_per_root, file_bytes', [(16, logs.FILE_BYTES), (17, 1)])
def test_startup_log_budget_is_global_and_oldest_first(tmp_path, log_io, files_per_root, file_bytes):
    roots = [tmp_path/'one', tmp_path/'two']
    payload = b'x'*file_bytes
    for index, root in enumerate(roots):
        root.mkdir()
        entries = {}
        for i in range(files_per_root):
            path = root/f'events-{i}.jsonl'
            path.write_bytes(payload)
            st = path.stat()
            entries[path.name] = dict(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                identity=[st.st_dev, st.st_ino], writtenAt=index+1)
        (root/logs.REGISTRY).write_text(json.dumps(dict(schema='diagnostic-ownership-v1', files=entries)))
    unknown = roots[0]/'events-user.jsonl'
    unknown.write_bytes(b'keep')
    for root in roots:
        logs.prune_owned_logs(root, now=3)
    managed = [p for root in roots for p in root.glob('events-*.jsonl') if p != unknown]
    assert sum(p.stat().st_size for p in managed) <= logs.ROOT_BYTES
    assert len(managed) <= logs.ROOT_FILES
    assert not (roots[0]/'events-0.jsonl').exists()
    assert (roots[1]/'events-0.jsonl').exists()
    assert unknown.read_bytes() == b'keep'


@pytest.mark.parametrize('namespace', ['automatic-v2', 'full-v2'])
def test_registered_expiry_preserves_replaced_image(tmp_path, namespace):
    with image_io(), patch.object(images, '_start_retention_worker'):
        with images.automatic_storage_transaction(tmp_path, create=True, namespace=namespace):
            relative = f'failures/{namespace}/auto-'+'3'*32+'.png'
            row = dict(event='automatic-failure-sample', recordedAt='2020-01-01T00:00:00+00:00', samplePath=relative)
            row.update(images.create_automatic_image(tmp_path, relative, b'crop', namespace=namespace))
            images.replace_automatic_journal(images._directory(tmp_path, namespace), (json.dumps(row)+'\n').encode())
        path = tmp_path/relative
        path.rename(tmp_path/'original.png')
        path.write_bytes(b'crop')
        reports = images.prune_registered_failure_roots(now=1577836800+8*86400)
        assert not reports[(tmp_path, namespace)]['complete']
        assert path.read_bytes() == b'crop'


def test_periodic_worker_runs_registered_cleanup_after_low_frequency_wait():
    class StopLoop(Exception):
        pass
    with patch.object(images.threading, 'Event') as event, patch.object(images, 'prune_registered_failure_roots') as prune:
        event.return_value.wait.side_effect = [None, StopLoop]
        with pytest.raises(StopLoop):
            images._retention_loop()
        prune.assert_called_once_with()
        assert event.return_value.wait.call_args_list[0].args == (3600,)
