"""Acceptance predicates reject missing, stale, or unverified resource evidence."""
from types import SimpleNamespace as NS
import unittest
from probe_client import ResourceContextAcceptance as Probe
from season_context import modes_for_season

class ProbeResourceContracts(unittest.TestCase):
    def client(self):
        return NS(session=NS(busy=False, appliedSeason="s1", appliedMode="nightmare",
            currentSeason="s1", currentMode="nightmare", snapshot={}),
            settings=NS(busy=False), resources=NS(busy=False, inventoryVerified=False),
            package_manager=NS(busy=False), coordinator=NS(_pending_context=None),
            runtime=NS(snapshot=lambda: dict(season="s1", mode="nightmare")),
            providers=NS(capabilities=lambda season: {"nightmare": {"available": True}}))

    def probe(self, client=None, clock=lambda: 0):
        return Probe(client or self.client(), lambda *a: None, lambda *a: None,
                     dict(gameSeason="s1", gameMode="nightmare"), clock)

    def test_context_targets_cover_active_modes_and_restore_original(self):
        expected = {(season, mode) for season in ("s0", "s1")
                    for mode in modes_for_season(season)}
        self.assertEqual(set(Probe.contexts), expected)
        self.assertNotIn(("s1", "difficulty2"), Probe.contexts)
        for original in sorted(expected):
            with self.subTest(original=original):
                p = Probe(self.client(), lambda *a: None, lambda *a: None,
                          dict(gameSeason=original[0], gameMode=original[1]))
                self.assertEqual(set(p.targets), expected)
                self.assertEqual(p.targets[-1], original)

    def test_explicit_verification_still_rejects_missing_or_bad_builtins(self):
        for rows in ([], [dict(packageId="native", builtin=True, status="incomplete")],
                     [dict(packageId="other", builtin=True, status="bundled")]):
            with self.subTest(rows=rows):
                c = self.client()
                checks = {}
                p = Probe(c, lambda key, value: checks.update({key: value}), lambda *a: None,
                          dict(gameSeason="s1", gameMode="nightmare"))
                p.expected_ids = ["native"]
                p.rows = lambda: rows
                c.resources.inventoryVerified = True
                c.resources.resourcesChanged = NS(emit=lambda: None)
                c.coordinator.navigate = lambda *a: None
                p.go("verified")
                p.advance()
                self.assertFalse(checks["resource_explicit_verification_complete"])

    def test_declarations_cannot_claim_counts_or_verified_state(self):
        good=dict(packageId="native", builtin=True, status="declared", canRemove=False)
        self.assertTrue(Probe.declarations([good]))
        self.assertFalse(Probe.declarations([]))
        for extra in [dict(status="bundled"), dict(canRemove=True), dict(verified=True),
                      dict(mapCount=1), dict(layerCount=1), dict(bundledFiles=1)]:
            with self.subTest(extra=extra):
                self.assertFalse(Probe.declarations([dict(good, **extra)]))

    def test_verified_builtin_ids_ignore_uninstalled_optional_declarations(self):
        rows=[dict(packageId="native", builtin=True, status="bundled"),
              dict(packageId="absent-author", status="not-installed")]
        self.assertEqual(Probe.builtin_ids(rows), ["native"])

    def test_context_needs_matching_runtime_and_prepared_resource_provider(self):
        c=self.client(); p=self.probe(c)
        self.assertTrue(p.context_ready(("s1", "nightmare")))
        c.runtime.snapshot=lambda: dict(season="s0", mode="nightmare")
        self.assertFalse(p.context_ready(("s1", "nightmare")))
        c.runtime.snapshot=lambda: dict(season="s1", mode="nightmare")
        c.providers.capabilities=lambda season: {"nightmare": {"available": False}}
        self.assertFalse(p.context_ready(("s1", "nightmare")))

    def test_saved_preference_or_pending_context_is_not_readiness(self):
        c=self.client(); p=self.probe(c)
        c.session.appliedMode="difficult"
        self.assertFalse(p.context_ready(("s1", "nightmare")))
        c.session.appliedMode="nightmare"; c.session.snapshot={"contextPending": True}
        self.assertFalse(p.context_ready(("s1", "nightmare")))

    def test_startup_waits_for_explicit_start_command_and_applied_context(self):
        c=self.client();c._initialized=True;c.backend=NS(busy=False)
        p=self.probe(c)
        self.assertFalse(p.startup_ready())
        c.session.snapshot={"commands": {"start_round": {"available": True}}}
        self.assertTrue(p.startup_ready())
        c.session.appliedSeason="s0"
        self.assertFalse(p.startup_ready())
        c.session.appliedSeason="s1";c.backend.busy=True
        self.assertFalse(p.startup_ready())

    def test_resource_page_needs_completed_service_and_actual_qml_rows(self):
        c=self.client();c.resources.packageRows=[dict(packageId="native")]
        c.main=NS(findChild=lambda *a: NS(property=lambda key: 0))
        p=self.probe(c)
        self.assertFalse(p.list_ready())
        c.main=NS(findChild=lambda *a: NS(property=lambda key: 1))
        self.assertTrue(p.list_ready())
        c.resources.busy=True
        self.assertFalse(p.list_ready())

    def test_timeout_is_bounded_and_not_an_implicit_pass(self):
        clock=[0];p=self.probe(clock=lambda:clock[0]);p.go("verified");clock[0]=61
        with self.assertRaisesRegex(TimeoutError, "verified"):p.advance()

    def test_busy_strict_verification_does_not_complete_early(self):
        p=self.probe();p.go("verified")
        p.client.resources.busy=True
        self.assertFalse(p.advance())
        self.assertEqual(p.stage, "verified")

if __name__ == "__main__":unittest.main()
