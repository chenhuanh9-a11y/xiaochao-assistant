"""Exercise the already-built real-payload trial, only inside its baked test scope.

Never launches the application's normal production IPC entry, enables its
watcher, or accesses a user's existing install. Keep all failure evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from qtifw_trial_helper import scoped, verify
from probe_qtifw_windows import WindowsProbe, DATA


class TrialProbe:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.record = json.loads((self.root / "qtifw-build-record.json").read_bytes())
        self.policy = json.loads((self.root / "qtifw-trial-policy.json").read_bytes())
        self.setup = Path(self.record["setup"])
        self.output = self.root / "validation"
        self.output.mkdir()
        self.runs = []
        self.report = dict(realPayload=True, payloadSource=self.record["payloadSource"],
            normalProductionEntryLaunched=False, liveGameValidated=False, installedUserTreeTouched=False)

    def target(self, name):
        return scoped(self.policy, self.root / "targets" / name)

    def run(self, label, args, timeout=180):
        path = self.output / (label + ".log")
        with path.open("wb") as stream:
            result = subprocess.run([str(p) for p in args], stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        self.runs.append(dict(label=label, exitCode=result.returncode))
        return result.returncode

    def install(self, label, target, *values):
        return self.run(label, [self.setup, "--reject-messages", "--confirm-command", "install", "TrialTarget=" + str(target), *values])

    def seed(self, target):
        work = target / "program/work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "trial-personal.txt").write_bytes(b"Dedicated synthetic trial data\n")
        (target / "outside-work-personal.txt").write_bytes(b"Must remain outside clear scope\n")

    def purge(self, label, target, choice):
        code = self.run(label, [target / "trial-maintenance.exe", "--reject-messages", "--confirm-command",
                                "purge", "DataChoice=" + choice])
        assert code == 0, "Native uninstall failed"
        result = json.loads((target / "trial-data.json").read_bytes())
        assert result["success"] and result["dataChoice"] == choice, "Data callback did not complete"
        assert not (target / "program/星图.exe").exists()
        assert (target / "outside-work-personal.txt").read_bytes() == b"Must remain outside clear scope\n"
        assert (target / "program/work/trial-personal.txt").exists() == (choice == "keep")
        return result

    def native_guards(self, target):
        tool = target / "trial-maintenance.exe"
        assert self.run("invalid-data-choice", [tool, "--reject-messages", "--confirm-command", "purge", "DataChoice=invalid"]) != 0
        assert (target / "program/星图.exe").is_file()
        code = ("import sys;sys.path.insert(0,sys.argv[1]);from resource_transaction import resource_runtime_lease;"
                "lease=resource_runtime_lease(sys.argv[2]);print('ready',flush=True);sys.stdin.readline();lease.close()")
        process = subprocess.Popen([sys.executable, "-c", code, str(Path(__file__).resolve().parents[1] / "app"), str(target / "program")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert process.stdout.readline().strip() == "ready"
            assert self.run("busy-runtime-purge-rejected", [tool, "--reject-messages", "--confirm-command", "purge", "DataChoice=clear"]) != 0
            assert (target / "program/星图.exe").is_file()
            assert (target / "program/work/trial-personal.txt").is_file()
        finally:
            process.communicate("finish\n", timeout=10)
        link = target / "fault-injected-hardlink.txt"
        os.link(target / "program/work/trial-personal.txt", link)
        try:
            assert self.run("linked-data-purge-rejected", [tool, "--reject-messages", "--confirm-command", "purge", "DataChoice=clear"]) != 0
            assert (target / "program/星图.exe").is_file()
            assert link.read_bytes() == b"Dedicated synthetic trial data\n"
        finally:
            link.unlink()  # Remove only this test-created fault, not the data.
        return dict(invalidChoiceRejected=True, activeLeaseRejected=True, linkedDataRejected=True, programPreserved=True)

    def interruption(self, name, fault):
        target = self.target(name)
        log = (self.output / (name + "-interrupt.log")).open("wb")
        process = subprocess.Popen([str(self.setup), "--reject-messages", "--confirm-command", "install",
            "TrialTarget=" + str(target), "TrialFault=" + fault], stdout=log, stderr=subprocess.STDOUT)
        try:
            checkpoint = target / "trial-checkpoint.json"
            deadline = time.monotonic() + 150
            while not checkpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.1)
            assert checkpoint.exists(), "Real installer did not reach injected checkpoint"
        finally:
            if process.poll() is None:
                process.terminate()  # The exact process this test started.
            process.wait(timeout=15)
            log.close()
        time.sleep(22)  # Bounded support child exits itself; no process-name kill.
        assert not (target / "trial-maintenance.exe").exists()
        self.seed(target)
        before = (target / "program/work/trial-personal.txt").read_bytes()
        assert self.install(name + "-retry", target) == 0
        checked = verify(self.policy, target)
        result = json.loads((target / "trial-prepare.json").read_bytes())
        assert (target / "program/work/trial-personal.txt").read_bytes() == before
        assert (target / "trial-maintenance.exe").is_file()
        if fault == "after-extract":
            assert len(result["quarantined"]) == len(self.policy["files"])
            preserved = list((target / "recovery-files").glob("*/星图.exe"))
            assert preserved and preserved[0].is_file()
        self.seed(target)
        self.purge(name + "-purge-keep", target, "keep")
        return dict(interruptedExit=process.returncode, retryVerified=checked,
                    quarantined=len(result["quarantined"]), userDataPreserved=True,
                    maintenanceCreated=True, uninstallAfterRetryVerified=True)

    def migrations(self):
        fixtures = self.root / "legacy-fixtures"
        fixtures.mkdir()
        compiler = Path(r"E:\codex\tools\inno-setup-6.7.3\compiler\ISCC.exe")
        # Reuse the already-validated Inno fixture builder, with only this
        # trial's allowed targets. This is not a production discovery adapter.
        def fixture_run(label, args):
            return SimpleNamespace(returncode=self.run(label, args))
        fixture = SimpleNamespace(root=fixtures, targets=self.root / "targets",
            identity=self.record["instance"], inno=compiler, run=fixture_run)
        results = []
        for case, new_directory, fail in (("same", False, False), ("new", True, False), ("failed", False, True)):
            old = self.target("legacy-" + case)
            setup, uninstaller, entry = WindowsProbe.inno_fixture(fixture, "legacy-" + case, old)
            old_work = old / "work"
            old_work.mkdir()
            for name, value in DATA.items():
                (old_work / name).write_bytes(value)
            target = self.target("migrated-new-directory") if new_directory else old
            assert self.install("legacy-" + case + "-guard", old) != 0
            assert uninstaller.exists() and not (old / ".qtifw-trial-owner.json").exists()
            assert self.run("legacy-" + case + "-uninstall", [uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]) == 0
            deadline = time.monotonic() + 15
            while (old / "unins000.exe").exists() or (old / "unins000.dat").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Legacy self-removal did not complete")
                time.sleep(.1)
            code = self.install("legacy-" + case + "-qt-install", target, *( ["TrialFault=fail"] if fail else [] ))
            assert all((old_work / name).read_bytes() == value for name, value in DATA.items())
            if fail:
                assert code != 0
                assert self.run("legacy-known-setup-recovery", [setup, "/CURRENTUSER", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=" + str(old)]) == 0
                assert (old / "program/legacy.txt").read_bytes() == b"Synthetic Inno v0, not Xingtu.\n"
                results.append(dict(case=case, dataPreserved=True, explicitLegacyReinstall=True, automaticRollback=False))
                continue
            assert code == 0
            verify(self.policy, target)
            self.seed(target)
            for name, value in DATA.items():
                (target / "program/work" / name).write_bytes((old_work / name).read_bytes())
            choice = "clear" if new_directory else "keep"
            self.purge("legacy-" + case + "-qt-purge", target, choice)
            assert all((old_work / name).read_bytes() == value for name, value in DATA.items())
            assert (target / "program/work/settings.json").exists() == (choice == "keep")
            results.append(dict(case=case, legacyRegistryMatched=True, dataCopied=True,
                oldDataCopyRetained=True, targetChoice=choice, actualProductInnoMigrated=False))
        return results

    def execute(self):
        try:
            target = self.target("real-payload 中文")
            assert self.install("real-install", target) == 0
            self.report["installed"] = verify(self.policy, target)
            program = target / "program"
            self.seed(target)
            # The production binary's existing probe namespace isolates IPC and
            # disables native game enumeration, input, capture and network update.
            probe = self.output / "client-ui"
            code = self.run("client-ui-probe", [program / "星图.exe", "--probe-output", probe], timeout=360)
            state = json.loads((probe / "client-probe.json").read_bytes())
            self.report["uiProbe"] = dict(exitCode=code, allAssertionsPass=state["all_assertions_pass"],
                assertions=state["assertions"], errors=state["errors"], scope=state.get("scope"))
            assert code == 0 and state["all_assertions_pass"], "Installed full-client probe failed"
            assert self.run("client-self-check", [program / "星图.exe", "--self-check", "--verify-resources"], timeout=120) == 0
            check = json.loads((program / "work/client-self-check.json").read_bytes())
            assert check["ready"] and not check["captureStarted"] and not check["nativeInputStarted"]
            self.report["selfCheck"] = check
            helpers = {}
            for name in ("star-map-import-helper.exe", "star-map-recovery-helper.exe", "star-map-reset-helper.exe"):
                helpers[name] = self.run(name + "-help", [program / name, "--help"])
                assert helpers[name] == 0
            helpers["XingtuGameWatcher.exe --status"] = self.run("watcher-readonly-status", [program / "XingtuGameWatcher.exe", "--status"])
            assert helpers["XingtuGameWatcher.exe --status"] == 0
            self.report["helperEntryChecks"] = helpers
            self.report["postRuntimeInventory"] = verify(self.policy, target)
            self.report["nativeAdmission"] = self.native_guards(target)
            self.report["keepUninstall"] = self.purge("keep-uninstall", target, "keep")
            assert self.install("same-target-reinstall", target) == 0
            self.report["reinstall"] = verify(self.policy, target)
            self.report["clearUninstall"] = self.purge("clear-uninstall", target, "clear")
            self.report["beforeExtractRecovery"] = self.interruption("interrupt-before", "before-extract")
            self.report["afterExtractRecovery"] = self.interruption("interrupt-after", "after-extract")
            self.report["realPayloadFixtureMigration"] = self.migrations()
            self.report["completed"] = True
        except BaseException as error:
            self.report.update(completed=False, failure=type(error).__name__ + ": " + str(error))
            raise
        finally:
            self.report["runs"] = self.runs
            (self.output / "result.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-root", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows only")
    print(json.dumps(TrialProbe(args.trial_root).execute(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
