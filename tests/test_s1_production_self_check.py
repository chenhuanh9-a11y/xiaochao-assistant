"""Formal self-check closes missing-context false success; no EXE or game IO.

main(), the native inventory/hash validator and the production read lease are
real. Frozen process constants and identity/root are fixtures. Actual S0 report
construction uses EngineFactories with only engine construction replaced. The
positive S1 fixture has complete real inventories but synthetic image/NPZ bytes;
its final typed-provider preparation is replaced, never claimed as native QA.
"""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sys
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / folder) for folder in ('app', 'tools', 'tests', 'experiments/qt_quick')]
import client
import client_providers
import managed_base_resources as managed
import packaged_product_identity as identity
import s1_candidate_purpose as purpose
import s1_private_resources as native
import test_s1_private_packaging as fixtures

S0 = ['s0:nightmare', 's0:difficult']
FOUR = S0 + ['s1:difficult', 's1:nightmare']
REAL_NATIVE = object()


class ProductionSelfCheckTests(unittest.TestCase):
    def setUp(self):
        # Resource validation is independent of release activation.
        self.enterContext(patch.object(managed, "_REVIEWED_S1_BASE_RELEASES", ()))
        self.enterContext(patch.object(purpose, "_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED", False))
        temp = tempfile.TemporaryDirectory(prefix='formal-self-check-fixture-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.resources = self.root / '_internal'
        self.resources.mkdir()
        self.profile = dict(product='星图', installerInstance=dict(id='production', product='星图',
            appId=identity.PRODUCTION_APP_ID), includedResourceContexts=list(FOUR))
        self.registry = client_providers.build_engine_factories(self.resources)
        self.registry._build = lambda mode, *, season='s0': SimpleNamespace(
            pack=SimpleNamespace(maps=[]), identity_floors=('1',))
        self.native_registry = None
        self.factory = self.verify = self.failure = None

    def packs(self):
        fixture = fixtures.PrivatePackagingTests()
        fixture.root, fixture.resources = self.root, self.resources
        self.native_registry = fixture.packs()
        shutil.rmtree(self.resources / "seasons/s1/difficulty2")
        return self.native_registry

    def invoke(self, *, native_result=REAL_NATIVE, frozen=True, production=True, entry=False):
        (self.resources / 'installation-profile.json').write_text(json.dumps(self.profile), encoding='utf-8')
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', ['client', '--sandbox', str(self.root),
                '--self-check', '--verify-resources']))
            for name, value in dict(FROZEN=frozen, PRODUCTION=production, RESOURCE_ROOT=self.resources,
                                    WORK_ROOT=self.root / 'work', owned_sandbox=lambda ignored: self.root).items():
                stack.enter_context(patch.object(client, name, value))
            for target in ('client.QGuiApplication', 'client.QtClient',
                           'client_input.NativeShortcutBackend', 'client_capture.ClientCaptureProvider'):
                stack.enter_context(patch(target, side_effect=AssertionError('self-check must not start ' + target)))
            # Separate actual S0 report machinery from the final S1 typed fixture.
            self.factory = stack.enter_context(patch('client_providers.build_engine_factories',
                side_effect=[self.registry, self.native_registry]))
            if native_result is REAL_NATIVE:
                self.verify = stack.enter_context(patch.object(native, 'validated_s1_resources',
                    wraps=native.validated_s1_resources))
            else:
                self.verify = stack.enter_context(patch.object(native, 'validated_s1_resources',
                    return_value=native_result))
            stack.enter_context(redirect_stdout(output))
            if entry:
                import qt_client_entry
                stack.enter_context(patch.object(identity, 'baked_identity', return_value='qt-production'))
                self.failure = stack.enter_context(patch.object(qt_client_entry, 'report_startup_failure'))
                status = qt_client_entry.run()
            else:
                status = client.main()
        return status, json.loads(output.getvalue()) if output.getvalue().strip() else None

    def synthetic_native_result(self):
        return dict(included=True, providers={mode: dict(available=True, prepared=True,
            season='s1', mode=mode, liveReady=False, liveAuthorization=False) for mode in native.S1_RUNTIME_MODES})

    def test_complete_four_context_fixture_succeeds_with_real_native_inventory_checks(self):
        self.packs()
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertIs(result['ready'], True)
        self.assertEqual(set(result['resourceContexts']), set(FOUR))
        self.verify.assert_called_once_with(self.resources, required=True, modes=native.S1_RUNTIME_MODES)
        self.assertEqual([call.args[0] for call in self.native_registry.prepare_context.call_args_list], ['difficult', 'nightmare'])
        self.assertIs(result['s1Included'], True)
        for flag in ('nativeInputStarted', 'windowsCreated', 'listenersStarted', 'captureStarted'):
            self.assertIs(result[flag], False)
        self.assertEqual(json.loads((self.root / 'work/client-self-check.json').read_text()), result)
        self.assertFalse((self.root / managed.STATE).exists())
        self.assertEqual(managed._REVIEWED_S1_BASE_RELEASES, ())
        self.assertIs(purpose._PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED, False)

    def test_entire_declared_s1_absence_rejects_not_s0_only_success(self):
        with self.assertRaisesRegex(ValueError, 'requires all declared'):
            self.invoke()
        self.verify.assert_called_once_with(self.resources, required=True, modes=native.S1_RUNTIME_MODES)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse((self.root / 'work/client-self-check.json').exists())
        self.assertFalse((self.root / managed.STATE).exists())

    def test_one_missing_native_mode_rejects_before_typed_provider(self):
        self.packs()
        (self.resources / 'seasons/s1/difficult').rename(self.root / 'removed-fixture-mode')
        with self.assertRaisesRegex(ValueError, 'complete exact'):
            self.invoke()
        self.native_registry.prepare.assert_not_called()

    def test_current_native_hash_corruption_still_rejects_and_preserves_bytes(self):
        self.packs()
        path = next((self.resources / 'seasons/s1/difficult/references/entries').glob('*.npz'))
        path.write_bytes(b'owned negative fixture corruption')
        with self.assertRaisesRegex(ValueError, 'size/hash mismatch'):
            self.invoke()
        self.assertEqual(path.read_bytes(), b'owned negative fixture corruption')
        self.native_registry.prepare.assert_not_called()
        self.assertFalse((self.root / managed.STATE).exists())

    def test_extra_native_path_still_rejects(self):
        self.packs()
        path = self.resources / 'seasons/s1/difficult/references/unlisted.json'
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            self.invoke()
        self.assertEqual(path.read_text(), '{}')
        self.native_registry.prepare.assert_not_called()

    def test_profile_declared_contexts_must_be_complete_unique_and_typed(self):
        bad_values = [None, {}, 's1:nightmare', [], FOUR[:-1], FOUR + [FOUR[-1]],
                      FOUR + ['s1:unknown'], [*S0, 's2:nightmare'], [*FOUR[:-1], {}]]
        for value in bad_values:
            with self.subTest(value=value):
                self.profile['includedResourceContexts'] = value
                with self.assertRaisesRegex(ValueError, 'production.*context declaration'):
                    self.invoke()
                self.verify.assert_not_called()

    def test_one_reported_s1_context_missing_or_extra_rejects(self):
        for mode in native.S1_RUNTIME_MODES:
            result = self.synthetic_native_result()
            result['providers'].pop(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'context missing or extra'):
                self.invoke(native_result=result)
        result = self.synthetic_native_result()
        result['providers']['unknown'] = dict(available=True)
        with self.assertRaisesRegex(ValueError, 'context missing or extra'):
            self.invoke(native_result=result)

    def test_native_inclusion_requires_literal_true(self):
        for value in (False, None, 1, 'true'):
            result = self.synthetic_native_result()
            result['included'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'context missing or extra'):
                self.invoke(native_result=result)

    def test_context_rows_require_contract_fields_not_truthy_substitutes(self):
        for field, value in (('available', False), ('available', 1), ('prepared', False),
                             ('prepared', 1), ('season', 's0'), ('mode', 'difficult')):
            result = self.synthetic_native_result()
            result['providers']['nightmare'][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'unavailable or misbound'):
                self.invoke(native_result=result)
        result = self.synthetic_native_result()
        del result['providers']['nightmare']['prepared']
        with self.assertRaisesRegex(ValueError, 'unavailable or misbound'):
            self.invoke(native_result=result)

    def test_absent_s0_report_fails_complete_four_context_check(self):
        self.registry.capabilities = lambda season='s0': {'nightmare': dict(available=True, prepared=True, season='s0', mode='nightmare')}
        with self.assertRaisesRegex(ValueError, 'context missing or extra'):
            self.invoke(native_result=self.synthetic_native_result())

    def test_s0_only_and_legacy_profiles_keep_optional_s1_behavior(self):
        for declared in (S0, list(reversed(S0)), None):
            self.profile.pop('includedResourceContexts', None)
            if declared is not None:
                self.profile['includedResourceContexts'] = declared
            with self.subTest(declared=declared):
                status, result = self.invoke()
                self.assertEqual(status, 0)
                self.assertIs(result['ready'], True)
                self.assertFalse(result['s1Included'])
                self.assertEqual(set(result['resourceContexts']), set(S0))
                self.verify.assert_called_once_with(self.resources, required=False, modes=native.S1_RUNTIME_MODES)

    def test_private_profiles_still_require_native_resources(self):
        for instance in identity.PRIVATE_S1_INSTANCES:
            self.profile = dict(installerInstance=dict(id=instance))
            with self.subTest(instance=instance), self.assertRaisesRegex(ValueError, 'requires all declared'):
                self.invoke(production=False)
            self.verify.assert_called_once_with(self.resources, required=True, modes=native.S1_MODES)

    def test_source_self_check_does_not_adopt_frozen_profile_authority(self):
        status, result = self.invoke(frozen=False, production=False)
        self.assertEqual(status, 0)
        self.assertFalse(result['s1Included'])
        self.verify.assert_called_once_with(self.resources, required=False, modes=native.S1_RUNTIME_MODES)

    def test_staged_candidate_does_not_replace_missing_active_s1_during_self_check(self):
        candidate = self.root / managed.CANDIDATE / 'payload/_internal/seasons/s1/difficult/native-map-manifest.json'
        candidate.parent.mkdir(parents=True)
        candidate.write_text('{"candidate":"not active"}')
        with self.assertRaisesRegex(ValueError, 'requires all declared'):
            self.invoke()
        self.assertEqual(candidate.read_text(), '{"candidate":"not active"}')
        self.assertFalse((self.resources / 'seasons').exists())
        self.assertFalse((self.root / managed.STATE).exists())

    def test_pending_install_transaction_blocks_before_any_provider_or_native_validation(self):
        pending = self.root / 'work/map-packages/pending-additions.json'
        pending.parent.mkdir(parents=True)
        pending.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'additions-recovery-required'):
            self.invoke()
        self.factory.assert_not_called()
        self.verify.assert_not_called()
        self.assertEqual(pending.read_text(), '{}')

    def test_qt_free_entry_returns_failure_for_absent_formal_resources(self):
        status, result = self.invoke(entry=True)
        self.assertEqual(status, 1)
        self.assertIsNone(result)
        self.failure.assert_called_once()
        self.assertEqual(self.failure.call_args.kwargs, dict(stage='run-client'))
        self.assertFalse((self.root / 'work/client-self-check.json').exists())


if __name__ == '__main__':
    unittest.main()
