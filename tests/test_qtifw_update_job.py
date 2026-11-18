"""Real owned helper processes; no installer, application or user data is run."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import shutil
import sys
import subprocess
from unittest.mock import patch

import qtifw_update_job as job
import qtifw_trial_helper as helper


@unittest.skipUnless(os.name == "nt", "Exact native Windows process identity")
class NativeUpdateJobTests(unittest.TestCase):
    def test_actual_helper_start_entry_publishes_verified_terminal_failure(self):
        with tempfile.TemporaryDirectory(prefix="星图 helper entry ") as temporary:
            support = Path(temporary)
            shutil.copy2(helper.__file__, support / "qtifw_trial_helper.py")
            shutil.copy2(job.__file__, support / "qtifw_update_job.py")
            (support / "qtifw-trial-policy.json").write_text(json.dumps(dict(
                instance="fixture", payloadSource="f" * 40, portable=True, allowNewTargets=True,
                upgradeCapable=True, targetVersion=dict(version="1.1.7"), files={})), encoding="utf-8")
            local = support / "LocalAppData"
            local.mkdir()
            result = support / "update-attempt-actual-helper.json"
            arguments = [str(support / "qtifw_trial_helper.py"), "bootstrap-start", str(support / "empty-target"),
                         "--result-file", str(result), "--setup-source", str(support / "never-executed.exe")]
            environment = dict(LOCALAPPDATA=str(local), PYTHONPATH=os.pathsep.join((str(Path(job.__file__).parent),
                str(Path(job.__file__).parents[1] / "app"))))
            starter = subprocess.run([sys.executable, "-B", *arguments], capture_output=True,
                                     env=dict(os.environ, **environment), check=True, timeout=10)
            accepted = json.loads(starter.stdout)
            self.assertTrue(accepted["started"])
            deadline = time.monotonic() + 10
            while not result.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            value = json.loads(result.read_text(encoding="utf-8"))
            self.assertFalse(value["success"])
            self.assertEqual(value["reason"], "native-fresh-setup-required")
            self.assertEqual(value["job"]["instance"], "fixture")
            self.assertFalse((support / "empty-target").exists())
            while job.status(accepted["pid"], accepted["created"])["running"] and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(job.status(accepted["pid"], accepted["created"])["running"])

    def test_real_detached_child_has_exact_identity_and_atomically_publishes_result(self):
        with tempfile.TemporaryDirectory(prefix="星图 update job ") as temporary:
            support = Path(temporary)
            shutil.copy2(job.__file__, support / "qtifw_update_job.py")
            worker = support / "qtifw_trial_helper.py"
            # Only native update work is replaced by a finite delay. Launch,
            # Windows creation-time identity, polling and publishing are real.
            worker.write_text("import sys,time,json\nfrom pathlib import Path\n"
                + "sys.path.insert(0," + repr(str(Path(job.__file__).parent)) + ")\n"
                + "from qtifw_update_job import publish\n"
                + "time.sleep(1)\n"
                + "publish({'instance':'fixture'},Path(sys.argv[2]),Path(sys.argv[4]),"
                + "{'success':True,'target':str(Path(sys.argv[2]).resolve())})\n", encoding="utf-8")
            target, result = support / "old target", support / "update-attempt-fixture.json"
            starter_script = support / "finite-starter.py"
            starter_script.write_text("import sys,json\n"
                + "sys.path.extend(" + repr([str(Path(job.__file__).parent), str(Path(job.__file__).parents[1] / "app")]) + ")\n"
                + "from qtifw_update_job import start\n"
                + "print(json.dumps(start({'instance':'fixture'},sys.argv[1],sys.argv[2],setup_source=sys.argv[3],interactive=True)))\n",
                encoding="utf-8")
            started = time.perf_counter()
            starter = subprocess.run([sys.executable, "-B", str(starter_script), str(target), str(result),
                str(support / "never-run-Setup.exe")], capture_output=True, check=True, timeout=10)
            self.assertLess(time.perf_counter() - started, .7)
            state = json.loads(starter.stdout)
            # The starter has actually exited; its independently owned child
            # must remain alive, then publish the result and exit naturally.
            self.assertTrue(job.status(state["pid"], state["created"])["running"])
            self.assertFalse(job.status(state["pid"], "another-process")["running"])
            deadline = time.monotonic() + 10
            while not result.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            value = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(value["success"])
            self.assertEqual(value["job"], {key: state[key] for key in ("instance", "target", "resultFile")})
            while job.status(state["pid"], state["created"])["running"] and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(job.status(state["pid"], state["created"])["running"])
            self.assertFalse(list(support.glob("*.new")))

    def test_already_published_attempt_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            support = Path(temporary)
            result = support / "update-attempt-old.json"
            result.write_text("{}", encoding="utf-8")
            with patch.object(job, "__file__", str(support / "qtifw_update_job.py")):
                with self.assertRaisesRegex(ValueError, "result-location-unavailable"):
                    job.start(dict(instance="fixture"), support / "old", result,
                              setup_source=support / "never-run.exe")


if __name__ == "__main__":
    unittest.main()
