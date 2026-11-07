"""Real QImage ownership and copy-on-write; synthetic arrays, no game/network."""
import gc
from concurrent.futures import ThreadPoolExecutor
import unittest
import weakref
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor
from client_coordinator import LiveImages


class OfflineTests(unittest.TestCase):
    def setUp(self):
        attempts = []
        def no_network(*args, **kwargs):
            attempts.append(True)
            raise AssertionError('Synthetic memory test attempted networking')
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                       'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=no_network)
            guard.start()
            self.addCleanup(guard.stop)
        self.addCleanup(lambda: self.assertFalse(attempts, 'Synthetic test attempted networking'))


class ImageMemoryTests(OfflineTests):
    def test_consumer_snapshot_shares_storage_but_mutation_detaches(self):
        images = LiveImages()
        pixels = np.full((48, 64, 4), 255, np.uint8)
        images.publish('source', pixels)
        first = images.requestImage('source', None, None)
        other = images.requestImage('source', None, None)
        self.assertIsNot(first, other)
        self.assertEqual(first.cacheKey(), other.cacheKey())
        first.fill(QColor(0, 0, 0, 255))
        self.assertNotEqual(first.cacheKey(), other.cacheKey())
        self.assertEqual(other.pixelColor(0, 0), QColor(255, 255, 255, 255))
        self.assertEqual(images.requestImage('source', None, None), other)

    def test_numpy_input_is_owned_once_and_not_retained(self):
        images = LiveImages()
        pixels = np.full((48, 64, 4), 255, np.uint8)
        weak = weakref.ref(pixels)
        images.publish('source', pixels, material_scope=('map', 'floor'))
        pixels.fill(0)
        self.assertEqual(images.requestImage('source', None, None).pixelColor(0, 0), QColor('white'))
        del pixels
        gc.collect()
        self.assertIsNone(weak())

    def test_eviction_and_clear_do_not_invalidate_existing_snapshot(self):
        images = LiveImages()
        pixels = np.full((12, 16, 4), 255, np.uint8)
        images.publish('source', pixels)
        retained = images.requestImage('source', None, None)
        for i in range(20):
            images.publish(str(i), np.zeros_like(pixels))
        images.clear()
        self.assertEqual(retained.pixelColor(0, 0), QColor('white'))
        size = QSize()
        self.assertTrue(images.requestImage('missing', size, QSize(1, 1)).isNull())
        self.assertEqual((size.width(), size.height()), (0, 0))

    def test_oversized_strided_input_rejected_before_allocation(self):
        pixels = np.broadcast_to(np.zeros((1, 1, 4), np.uint8), (5000, 5000, 4))
        with patch('numpy.ascontiguousarray', side_effect=AssertionError('must not allocate')):
            with self.assertRaises(ValueError):
                LiveImages().publish('invalid', pixels)

    def test_repeated_stage_accept_reject_keeps_count_byte_and_authorization_bounds(self):
        images = LiveImages()
        pixels = np.full((48, 64, 4), 255, np.uint8)
        reference = images.publish('reference:fixed', pixels)
        for i in range(3000):
            scope = ('nightmare' if i % 2 else 'difficult', str(i % 5), str(i % 3))
            images.publish(str(i), pixels, material_scope=scope)
            images.publish('pending:'+str(i), pixels, material_scope=scope)
            self.assertLessEqual(len(images._images), 3)
            self.assertLessEqual(sum(x.sizeInBytes() for x in images._images.values()), 3*pixels.nbytes)
            images.finish_staging(i, bool(i % 2))
            self.assertLessEqual(len(images._images), 2)
            images.clear_aligned()
            self.assertEqual(list(images._images), [reference.split('/')[-1]])
        images.clear()
        self.assertEqual(images._images, {})

    def test_parallel_reader_snapshots_survive_writer_replacement(self):
        images = LiveImages()
        pixels = np.full((64, 96, 4), 255, np.uint8)
        images.publish('source', pixels)
        def read():
            for _ in range(150):
                snapshot = images.requestImage('source', None, None)
                if not snapshot.isNull():
                    self.assertEqual((snapshot.width(), snapshot.height()), (96, 64))
                    snapshot.fill(QColor('black'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            readers = [pool.submit(read) for _ in range(2)]
            for _ in range(150):
                images.publish('source', pixels)
                self.assertEqual(images.requestImage('source', None, None).pixelColor(0, 0), QColor('white'))
            for reader in readers:
                reader.result()

    def test_const_bits_are_read_only_and_writable_bits_detach(self):
        images = LiveImages()
        images.publish('source', np.full((8, 12, 4), 255, np.uint8))
        first = images.requestImage('source', None, None)
        other = images.requestImage('source', None, None)
        self.assertTrue(first.constBits().readonly)
        self.assertEqual(first.cacheKey(), other.cacheKey())
        writable = first.bits()
        self.assertNotEqual(first.cacheKey(), other.cacheKey())
        writable[0] = 0
        self.assertEqual(first.pixelColor(0, 0).red(), 0)
        self.assertEqual(other.pixelColor(0, 0).red(), 255)
        self.assertEqual(images.requestImage('source', None, None).pixelColor(0, 0).red(), 255)

    def test_strided_and_converted_inputs_remain_owned_after_source_release(self):
        for dtype in (np.uint8, np.int16, np.float32):
            with self.subTest(dtype=dtype):
                source = np.zeros((12, 24, 4), dtype)
                source[:, :, 0] = 19
                source[:, :, 3] = 255
                pixels = source[:, ::2]
                images = LiveImages()
                url = images.publish('source', pixels, material_scope=('owned',))
                self.assertEqual(images.publish('equal', pixels, material_scope=('owned',)), url)
                retained = images.requestImage('source', None, None)
                source.fill(0)
                del pixels, source
                gc.collect()
                self.assertEqual(retained.pixelColor(0, 0), QColor(19, 0, 0, 255))

    def test_parallel_readers_survive_staging_and_clear(self):
        images = LiveImages()
        pixels = np.full((24, 32, 4), 255, np.uint8)
        def read():
            for _ in range(400):
                snapshot = images.requestImage('source', None, None)
                if not snapshot.isNull():
                    self.assertEqual(snapshot.pixelColor(0, 0), QColor('white'))
                    snapshot.bits()[0] = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            readers = [pool.submit(read) for _ in range(2)]
            for index in range(400):
                images.publish('source', pixels)
                retained = images.requestImage('source', None, None)
                images.publish('pending:' + str(index), pixels)
                images.finish_staging(index, bool(index % 2))
                images.clear_aligned()
                self.assertEqual(retained.pixelColor(0, 0), QColor('white'))
            for reader in readers:
                reader.result()
        self.assertEqual(images._images, {})


class SyntheticReopenTests(OfflineTests):
    def test_real_runtime_reopen_revokes_old_tokens_without_retaining_capture_history(self):
        import copy
        from client_live_runtime import LiveRuntime, _AlignmentHost
        from test_client_live_runtime import FixtureCapture, fixture_engine
        from s0_test_fixtures import synthetic_read
        for mode in ('nightmare', 'difficult'):
            capture = FixtureCapture()
            frame_refs = []
            live_frame_counts = []
            original_capture = capture.capture
            def tracked_capture(cancelled=None):
                result = original_capture(cancelled)
                frame_refs.append(weakref.ref(result.frame_bgr))
                return result
            capture.capture = tracked_capture
            engine = fixture_engine(mode)
            runtime = LiveRuntime(engine_factories={mode: lambda: engine}, capture_provider=capture,
                                  evidence_kind='synthetic', clock=lambda: 1)
            images = LiveImages()
            fit = dict(success=True, matrixSourceToClient=np.eye(3),
                selectedRouteCurrentFrameResidual=dict(verified=True, poseVerifiedAgainstCanonicalGeometry=True,
                    profileRenderDerivedFromCanonical=True, coordinatePackId='fixture-pack',
                    validationBasis='shared-canonical-geometry-current-frame'))
            def dispatch(action, **extra):
                state = runtime.snapshot()
                context = {key: state[key] for key in ('mode', 'profileId', 'roundId', 'frameId')}
                return runtime.dispatch(action, dict(context, generation=22, **extra))
            try:
                # Plain replacements deliberately avoid Mock call histories
                # retaining every synthetic frame during a long-loop test.
                with patch('map_interface_observation.detect_map_interface', new=lambda *a, **k: dict(detected=True)), \
                     patch('floor_control_region.locate_vertical_floor_controls', new=lambda frame, mode, **k: synthetic_read(mode)), \
                     patch.object(_AlignmentHost, 'fit', new=lambda *a, **k: copy.deepcopy(fit)):
                    self.assertTrue(dispatch('set_context', mode=mode, season='s0')['success'])
                    self.assertTrue(dispatch('start_round')['success'])
                    for cycle in range(300):
                        result = dispatch('capture_now')
                        self.assertTrue(result['success'], result)
                        token = result['renderToken']
                        payload = runtime.render_payload(token)
                        self.assertIsNotNone(payload)
                        url = images.publish(token, payload['pixels'], material_scope=(mode, payload['mapId'], payload['floor']))
                        image = images.requestImage(url.removeprefix('image://live/'), None, None)
                        self.assertFalse(image.isNull())
                        self.assertLessEqual(len(images._images), 2)
                        self.assertTrue(dispatch('close_map')['success'])
                        self.assertIsNone(runtime.render_payload(token))
                        images.clear_aligned()
                        if cycle % 25 == 0:
                            gc.collect()
                            live_frame_counts.append(sum(ref() is not None for ref in frame_refs))
                    self.assertEqual(engine.rank_count, 1)
                    self.assertTrue(dispatch('end_round')['success'])
                    self.assertIsNone(runtime.reference_payload())
                    self.assertEqual(runtime.snapshot()['core']['poses'], {})
                    self.assertFalse(capture.enabled)
                    self.assertEqual(images._images, {})
            finally:
                runtime.shutdown()
            gc.collect()
            # Warm solver/observation owners may retain several current frames,
            # but must not retain the history of 300 captures. One latest frame
            # intentionally survives round end for the floor-region editor.
            self.assertLessEqual(max(live_frame_counts[2:])-min(live_frame_counts[2:]), 1)
            self.assertLessEqual(live_frame_counts[-1], live_frame_counts[1]+1)
            self.assertEqual(sum(ref() is not None for ref in frame_refs), 1)


if __name__ == '__main__': unittest.main()
