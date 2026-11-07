"""Offline update-state regressions; no desktop imports or real network access."""
from pathlib import Path
from dataclasses import replace
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from update_service import (HISTORY_URL, MANIFEST_URL, RECHECK_SECONDS,
                            RETRY_SECONDS, UpdateService)


def release(version='1.1', code=11):
    return dict(schemaVersion=1, platform='windows', version=version,
                versionCode=code, releaseNotes='Fixture release', publishedAt='2026-10-27')


class FetchPlan:
    def __init__(self, *outcomes):
        self.outcomes = queue.Queue()
        for outcome in outcomes:
            self.outcomes.put(outcome)

    def __call__(self, url):
        if url == HISTORY_URL:
            return {'releases': []}
        if url != MANIFEST_URL:
            raise AssertionError('Unexpected fixture URL')
        value = self.outcomes.get_nowait()
        if isinstance(value, Exception):
            raise value
        return value


class UpdateServiceStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xingtu-update-state-')
        self.addCleanup(self.temporary.cleanup)
        # A regression cannot silently fall back to the real fetch function.
        network = patch('urllib.request.urlopen', side_effect=AssertionError('Real network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def service(self, *outcomes, current='1.0'):
        return UpdateService(current, Path(self.temporary.name) / 'preferences.json', fetch=FetchPlan(*outcomes))

    def drain(self, service, now):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            event = service.poll(now=now)
            if event is not None:
                return event
            threading.Event().wait(.001)
        self.fail('Fixture worker did not enqueue a result')

    def test_first_failure_becomes_retryable_error(self):
        service = self.service(RuntimeError('offline'))
        self.assertTrue(service.check())
        self.assertIn('error', self.drain(service, 100))
        self.assertEqual(service.phase, 'error')
        self.assertFalse(service._checking)
        self.assertIsNone(service.latest)
        self.assertIn('offline', service.message)
        self.assertEqual(service.next_check_at, 100 + RETRY_SECONDS[0])

    def test_automatic_failure_restores_cached_available_or_latest(self):
        for version, expected in (('1.1', 'available'), ('1.0', 'latest'), ('0.9', 'latest')):
            with self.subTest(cached_version=version):
                service = self.service(release(version), RuntimeError('offline'))
                self.assertTrue(service.check())
                self.drain(service, 100)
                cached, history = service.latest, list(service.history)
                self.assertTrue(service.check())
                self.assertEqual(service.phase, 'checking')
                self.assertIn('error', self.drain(service, 200))
                self.assertEqual(service.phase, expected)
                self.assertFalse(service._checking)
                self.assertIs(service.latest, cached)
                self.assertEqual(service.history, history)
                self.assertIn('offline', service.message)
                self.assertEqual(service.next_check_at, 200 + RETRY_SECONDS[0])

    def test_manual_retry_recovers_after_cached_automatic_failure(self):
        service = self.service(release(), RuntimeError('offline'), release('1.2', 12))
        service.check()
        self.drain(service, 100)
        service.check()
        self.drain(service, 200)
        self.assertNotEqual(service.phase, 'checking')
        self.assertTrue(service.check(manual=True))
        self.drain(service, 210)
        self.assertEqual(service.phase, 'available')
        self.assertEqual(service.latest.version, '1.2')
        self.assertEqual(service._failures, 0)
        self.assertEqual(service.next_check_at, 210 + RECHECK_SECONDS)

    def test_cached_manual_failure_still_reports_error(self):
        service = self.service(release(), RuntimeError('manual failure'))
        service.check()
        self.drain(service, 100)
        cached = service.latest
        service.check(manual=True)
        self.drain(service, 200)
        self.assertEqual(service.phase, 'error')
        self.assertFalse(service._checking)
        self.assertIs(service.latest, cached)

    def test_due_automatic_retry_recovers_and_resets_backoff(self):
        service = self.service(RuntimeError('offline'), release())
        service.check()
        self.drain(service, 100)
        due = service.next_check_at
        self.assertIsNone(service.poll(now=due))
        self.drain(service, due)
        self.assertEqual(service.phase, 'available')
        self.assertEqual(service._failures, 0)
        self.assertEqual(service.next_check_at, due + RECHECK_SECONDS)

    def test_worker_only_enqueues_until_poll_applies_result(self):
        entered, finish = threading.Event(), threading.Event()

        def fetch(url):
            if url == HISTORY_URL:
                return {'releases': []}
            self.assertEqual(url, MANIFEST_URL)
            entered.set()
            if not finish.wait(2):
                raise RuntimeError('Fixture release was not unblocked')
            return release()

        service = UpdateService('1.0', Path(self.temporary.name) / 'preferences.json', fetch=fetch)
        self.addCleanup(finish.set)
        self.assertTrue(service.check())
        self.assertTrue(entered.wait(1))
        self.assertFalse(service.check(manual=True))
        self.assertEqual(service.phase, 'checking')
        self.assertIsNone(service.latest)
        finish.set()
        deadline = time.monotonic() + 2
        while service._events.empty() and time.monotonic() < deadline:
            threading.Event().wait(.001)
        self.assertFalse(service._events.empty())
        self.assertEqual(service.phase, 'checking')
        self.assertIsNone(service.latest)
        self.drain(service, 100)
        self.assertEqual(service.phase, 'available')

    def test_current_version_notice_and_same_version_revision_are_new(self):
        service = self.service(release(), current='1.1')
        service.check()
        self.drain(service, 100)
        self.assertEqual(service.phase, 'latest')
        self.assertTrue(service.should_announce())
        first = service.latest
        service.mark_notes_seen()
        self.assertFalse(service.should_announce())
        self.assertFalse(service.has_unread)
        service.latest = replace(first, pages={'quark': 'https://pan.quark.cn/s/changed'}, size=999)
        self.assertFalse(service.should_announce(), 'Download metadata alone is not a new notice')
        service.latest = replace(first, notes='Corrected announcement')
        self.assertTrue(service.should_announce())
        self.assertTrue(service.has_unread)
        for changed in (replace(first, version_code=12), replace(first, published_at='2026-11-07')):
            service.latest = changed
            self.assertTrue(service.should_announce())

    def test_notice_identity_persists_but_legacy_version_record_does_not_hide_revision(self):
        service = self.service(release())
        service.check()
        self.drain(service, 100)
        service._stored = dict(seenNotesVersion='1.1', skippedVersion='1.1', unrelated='keep')
        self.assertTrue(service.should_announce())
        service.skip_latest()
        self.assertFalse(service.should_announce())
        service.latest = replace(service.latest, notes='New content for the same version')
        self.assertTrue(service.should_announce())
        service.mark_notes_seen()
        restarted = self.service()
        restarted.latest, restarted.phase = service.latest, 'available'
        self.assertFalse(restarted.should_announce())
        stored = json.loads(service.state_path.read_text())
        self.assertEqual(stored['unrelated'], 'keep')
        self.assertNotIn('New content', json.dumps(stored))

    def test_notice_records_are_validated_and_bounded(self):
        service = self.service(release())
        service.check()
        self.drain(service, 100)
        service._stored['seenAnnouncementIds'] = 'malformed'
        self.assertTrue(service.should_announce())
        service._stored['seenAnnouncementIds'] = ['invalid', None] + [f'{n:064x}' for n in range(70)]
        service.mark_notes_seen()
        self.assertEqual(len(service.seen_announcement_ids), 64)
        self.assertEqual(service.seen_announcement_ids[-1], service.latest.announcement_id)
        service.latest = replace(service.latest, notes='')
        self.assertFalse(service.should_announce())

    def test_failed_refresh_does_not_revoke_valid_unshown_notice(self):
        service = self.service(release(), OSError('fixture offline'))
        service.check()
        self.drain(service, 100)
        self.assertTrue(service.should_announce())
        service.check(manual=True)
        self.assertFalse(service.should_announce())
        self.drain(service, 200)
        self.assertEqual(service.phase, 'error')
        self.assertTrue(service.should_announce())


if __name__ == '__main__':
    unittest.main()
