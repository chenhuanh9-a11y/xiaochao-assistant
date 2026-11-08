"""Exercise the already-built real-payload trial, only inside its baked test scope.

Never launches the application's normal production IPC entry, enables its
watcher, or accesses a user's existing install. Keep all failure evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from qtifw_trial_helper import scoped, verify
from probe_qtifw_windows import WindowsProbe, DATA


class TrialProbe:
    def __init__(self, root, evidence_name="validation"):
        self.root = root.resolve(strict=True)
        self.record = json.loads((self.root / "qtifw-build-record.json").read_bytes())
        self.policy = json.loads((self.root / "qtifw-trial-policy.json").read_bytes())
        self.setup = Path(self.record["setup"])
        if (self.setup.parent != self.root or self.setup.stat().st_size != self.record["bytes"]
                or hashlib.sha256(self.setup.read_bytes()).hexdigest() != self.record["sha256"]):
            raise ValueError("Trial Setup does not match its build record")
        config = ET.parse(self.root / "config/config.xml").getroot()
        self.complete_removal = config.findtext("RemoveTargetDir") == "true"
        if self.complete_removal and not self.policy.get("fullUninstall"):
            raise ValueError("Legacy full-removal trial: use its original evidence, not the current probe")
        if not re.fullmatch(r"[A-Za-z0-9-]+", evidence_name):
            raise ValueError("Evidence name must be a simple local directory name")
        self.output = self.root / evidence_name
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

    def update_failure_repair(self, target):
        packages = self.output / "update-v2/packages"
        shutil.copytree(self.root / "packages", packages)
        for metadata in packages.glob("*/meta/package.xml"):
            tree = ET.parse(metadata)
            tree.getroot().find("Version").text = "2.0.0"
            tree.write(metadata, encoding="utf-8", xml_declaration=True)
        repository = self.output / "update-v2/repository"
        assert self.run("real-update-repository", [r"E:\codex\tools\qtifw-4.11.0\bin\repogen.exe", "-p", packages, repository]) == 0
        tool = target / "trial-maintenance.exe"
        common = [tool, "--set-temp-repository", repository.as_uri(), "--reject-messages", "--confirm-command"]
        sentinel = target / "program/work/trial-personal.txt"
        before = sentinel.read_bytes()
        failed = self.run("real-update-injected-failure", [*common, "update", "TrialFault=fail", "DataChoice=clear"])
        assert failed != 0 and sentinel.read_bytes() == before
        observed = dict(exitCode=failed, programPresent=(target / "program/星图.exe").exists(),
                        maintenancePresent=tool.exists(), dataPreserved=True)
        self.run("real-after-failed-update-list", [tool, "list"])
        assert self.run("real-update-explicit-repair", [*common, "install", "org.xingtu.qtifw.realtrial"]) == 0
        repaired = verify(self.policy, target)
        assert sentinel.read_bytes() == before
        assert self.run("real-after-repair-list", [tool, "list"]) == 0
        text = (self.output / "real-after-repair-list.log").read_bytes().decode("utf-8", errors="replace")
        assert "2.0.0" in text and "org.xingtu.qtifw.realtrial" in text
        return dict(failedUpdate=observed, explicitRepair=repaired, componentVersion="2.0.0",
                    productVersionUnchanged=True, automaticRollbackImplemented=False)

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

    def execute(self, *, resume_installed=False):
        if self.complete_removal:
            if resume_installed:
                raise ValueError("Complete-uninstall validation requires a new, owned test target")
            return self.execute_complete()
        try:
            target = self.target("real-payload 中文")
            if not resume_installed:
                assert self.install("real-install", target) == 0
            else:
                assert (target / "trial-maintenance.exe").is_file()
                self.report["existingInstallReused"] = True
            self.report["installed"] = verify(self.policy, target)
            program = target / "program"
            self.seed(target)
            (program / "work/.qt-client-probe-owner").write_text("qt-quick-build-probe-v1\n", encoding="utf-8")
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
            self.report["realUpdateFailureRepair"] = self.update_failure_repair(target)
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

    def execute_complete(self):
        """Current fresh-install policy; retention/migration above is historical."""
        try:
            target = self.target("complete-uninstall 中文")
            assert self.install("complete-new-install", target) == 0
            self.report["installed"] = verify(self.policy, target)
            self.seed(target)
            outside = target.parent / "complete-uninstall-external.txt"
            with outside.open("xb") as stream:
                stream.write(b"External file must remain\n")
            self.report["nativeAdmission"] = self.native_guards(target)
            tool = target / "trial-maintenance.exe"
            code = self.run("complete-native-purge", [tool, "--reject-messages", "--confirm-command", "purge"])
            assert code == 0, "Native complete uninstall failed"
            # QtIFW's VBS cleanup runs after the native process exits. A receipt
            # only schedules it; neither exit=0 nor that receipt proves removal.
            for _ in range(100):
                if not target.exists():
                    break
                time.sleep(.1)
            assert not target.exists(), "Complete uninstall left the target directory"
            assert outside.read_bytes() == b"External file must remain\n"
            receipt = json.loads((self.root / "last-uninstall.json").read_bytes())
            assert (receipt["success"] and receipt["status"] == "scheduled"
                    and receipt["nativeRemovalScheduled"] and Path(receipt["target"]) == target)
            self.report.update(completed=True, completeUninstallExitCode=code,
                               targetDirectoryRemoved=True, outsideFilePreserved=True,
                               runtimeChecksExecuted=False)
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
    parser.add_argument("--evidence-name", default="validation")
    parser.add_argument("--resume-installed", action="store_true", help="Reuse the byte-verified initial install after a probe preparation failure")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows only")
    print(json.dumps(TrialProbe(args.trial_root, args.evidence_name).execute(resume_installed=args.resume_installed), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
