"""The frame replay adapter validates inputs and never grants live authority."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from four_mode_frame_replay import CONTEXTS, load_manifest, _summary


class FourModeFrameReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self, contexts=CONTEXTS):
        rows = []
        for index, (season, mode) in enumerate(contexts):
            name = f"{season}-{mode}.png"
            image = np.full((24, 32, 3), (index, 60, 120), np.uint8)
            self.assertTrue(cv2.imwrite(str(self.root / name), image))
            rows.append(dict(season=season, mode=mode, frame=name,
                             sha256=hashlib.sha256((self.root / name).read_bytes()).hexdigest()))
        path = self.root / "manifest.json"
        path.write_text(json.dumps(dict(schema="xingtu-four-mode-frame-replay-v1", cases=rows)))
        return path

    def test_manifest_requires_all_four_distinct_contexts_and_verifies_frame_bytes(self):
        cases = load_manifest(self.manifest())
        self.assertEqual([(case.season, case.mode) for case in cases], list(CONTEXTS))
        path = self.manifest(CONTEXTS[:3])
        with self.assertRaisesRegex(ValueError, "four-contexts"):
            load_manifest(path)

    def test_manifest_rejects_frame_path_escape_and_modified_bytes(self):
        path = self.manifest()
        data = json.loads(path.read_text())
        data["cases"][0]["frame"] = "../outside.png"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "escapes"):
            load_manifest(path)
        path = self.manifest()
        (self.root / "s0-nightmare.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "sha256"):
            load_manifest(path)

    def test_summary_reports_decisions_without_frame_bytes_or_display_authority(self):
        case = load_manifest(self.manifest())[0]
        report = _summary(dict(season="s0", mode="nightmare", mapId="fixture", floor="1",
                               identityLocked=False, displayAuthorized=False, evidenceKind="recorded",
                               automaticCandidateRows=[dict(mapId="fixture", score=4.0)]),
                          [dict(event="identity-rank")], dict(success=True, reason="fit-unavailable"), case)
        self.assertFalse(report["liveAuthorization"])
        self.assertEqual(report["state"]["mapId"], "fixture")
        self.assertEqual(report["candidates"], [dict(mapId="fixture", score=4.0)])
        self.assertNotIn(str(case.frame_path), json.dumps(report))


if __name__ == "__main__":
    unittest.main()
