"""Native synthetic 1.1.2 -> 1.1.3 upgrade using the real frozen helper.

Never launches the synthetic Xingtu/C# executables or touches an existing
installation. All generated names, shortcuts, payload and backups are owned by
one new work directory. Includes repository outage and failure after real
old-component UNDO, new Extract and successful program verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "org.xingtu.qtifw.realtrial"
DATA_PACKAGE = "org.xingtu.qtifw.trialdata"
OWNER = ".qtifw-trial-owner.json"
PENDING = "program/work/pending-installer-upgrade.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def xml(path, root, fields):
    element = ET.Element(root)
    for key, value in fields.items():
        ET.SubElement(element, key).text = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(element)
    ET.ElementTree(element).write(path, encoding="utf-8", xml_declaration=True)


def versions(target):
    return {p.findtext("Name"): p.findtext("Version")
            for p in ET.parse(target / "components.xml").findall("Package")}


class NativeUpgrade:
    def __init__(self, args):
        self.args = args
        self.root = args.output.absolute()
        if (self.root.parent != ROOT / "work" or self.root.exists()
                or not self.root.name.startswith("qtifw-native-upgrade-20261113-")):
            raise ValueError("Output must be a NEW work/qtifw-native-upgrade-20261113-* directory")
        self.root.mkdir(parents=True)
        self.token = uuid.uuid4().hex
        self.name = "QtIFWNativeUpgrade_" + self.token
        write(self.root / ".synthetic-upgrade-scope", self.token)
        self.ifw = args.ifw_bin.resolve(strict=True)
        self.base = self.root / "build-installerbase.exe"
        shutil.copy2(self.ifw / "installerbase.exe", self.base)
        for name in ("temp", "desktop", "programs", "localappdata"):
            (self.root / name).mkdir()
        self.env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                        TEMP=str(self.root / "temp"), TMP=str(self.root / "temp"),
                        LOCALAPPDATA=str(self.root / "localappdata"))
        self.env.pop("XINGTU_QTIFW_UPGRADE_SUPPORT", None)
        self.env.pop("XINGTU_QTIFW_UPGRADE_BASE", None)
        self.env.pop("XINGTU_QTIFW_UPGRADE_RCC", None)
        self.env.pop("XINGTU_QTIFW_VERIFY_MAINTENANCE", None)
        self.report = dict(schema="qtifw-native-upgrade-synthetic-v1", syntheticOnly=True,
            actualProgramLaunched=False, realSetupValidated=False, name=self.name,
            output=str(self.root), subprocesses=[], cases={}, completed=False)
        self.targets = []

    def save(self):
        write(self.root / "result.json", json_bytes(self.report))

    def run(self, label, command, timeout=120, cwd=None):
        command = list(map(str, command))
        logpath = self.root / (label + ".log")
        record = dict(label=label, command=command, log=str(logpath), timeoutSeconds=timeout)
        self.report["subprocesses"].append(record)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        started = time.monotonic()
        try:
            with logpath.open("wb") as log:
                result = subprocess.run(command, cwd=cwd or self.root, env=self.env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW, timeout=timeout)
            record["exitCode"] = result.returncode
        except subprocess.TimeoutExpired:
            record.update(exitCode=None, timedOut=True)
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            self.save()
            print(json.dumps({k: record.get(k) for k in ("label", "exitCode", "seconds")}), flush=True)
        return record

    def source_snapshot(self):
        snapshot = self.root / "sources"
        revision = self.run("source-git-head", ["git", "rev-parse", "HEAD"], timeout=30, cwd=ROOT)
        assert revision["exitCode"] == 0
        self.report["sourceGitHead"] = Path(revision["log"]).read_text().strip()
        legacy = self.run("existing-ready-source", ["git", "show",
            self.report["sourceGitHead"] + ":app/resource_transaction.py"], timeout=30, cwd=ROOT)
        assert legacy["exitCode"] == 0
        legacy_bytes = Path(legacy["log"]).read_bytes()
        write(snapshot / "existing-ready/resource_transaction.py", legacy_bytes)
        self.report["existingReadySourceSha256"] = sha(legacy_bytes)
        paths = list((ROOT / "app").rglob("*.py"))
        paths += [ROOT / "tools" / name for name in
                  ("qtifw_trial_helper.py", "qtifw_trial_lease.py", "qtifw_upgrade.py", "qtifw_map_seed.py")]
        paths += list((ROOT / "tools/qtifw_trial").glob("*"))
        entries = {}
        for path in sorted(paths):
            if path.is_file():
                relative = path.relative_to(ROOT)
                data = path.read_bytes()
                write(snapshot / relative, data)
                entries[relative.as_posix()] = sha(data)
        self.report["frozenSourceFiles"] = entries
        self.report["baseSha256"] = sha(self.base.read_bytes())
        self.save()
        return snapshot

    def fixture(self, version, snapshot):
        folder = self.root / ("v" + version)
        folder.mkdir()
        source = sha(("synthetic-source-" + self.token + version).encode())[:40]
        product_instance = dict(id="production", appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1")
        values = {
            "星图.exe": ("synthetic Xingtu " + version + "; NEVER EXECUTE\n").encode(),
            "_internal/runtime.txt": ("runtime " + version + "\n").encode(),
            "_internal/installation-profile.json": json_bytes(dict(product="星图", sourceCommit=source,
                                                                  installerInstance=product_instance)),
            "release-report.json": json_bytes(dict(sourceCommit=source, displayVersion=version)),
        }
        if version == "1.1.2":
            values["obsolete-owned-v1.txt"] = b"This old native-owned file must disappear.\n"
        else:
            values["_internal/csharp-grid/StarMap.Grid.exe"] = b"Synthetic C# payload; NEVER EXECUTE\n"
        policy = dict(instance=uuid.uuid4().hex, payloadSource=source, portable=True,
            fullUninstall=True, allowNewTargets=True, faultsAllowed=False,
            files={name: dict(bytes=len(data), sha256=sha(data)) for name, data in values.items()})
        if version == "1.1.3":
            policy.update(upgradeCapable=True,
                targetVersion=dict(version=version, displayVersion=version, numericVersion="1.0.0.25"),
                targetIdentity=dict(sourceCommit=source, displayVersion=version, numericVersion="1.0.0.25",
                    executableSha256=sha(values["星图.exe"]), resourceInventorySha256="e" * 64))
        policy_path = folder / "qtifw-trial-policy.json"
        write(policy_path, json_bytes(policy))
        result = self.run("freeze-" + version, [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onedir", "--console", "--name", "qtifw-trial-helper", "--paths", snapshot / "app",
            "--paths", snapshot / "tools", "--exclude-module", "PySide6", "--exclude-module", "numpy",
            "--exclude-module", "cv2", "--exclude-module", "PIL", "--distpath", folder / "helper",
            "--workpath", folder / "freeze-work", "--specpath", folder,
            "--add-data", str(policy_path) + ";.", snapshot / "tools/qtifw_trial_helper.py"])
        assert result["exitCode"] == 0, "Freeze failed: " + version
        helper = folder / "helper/qtifw-trial-helper"
        support_files = sorted(p.relative_to(helper).as_posix() for p in helper.rglob("*") if p.is_file())
        qrc = ET.Element("RCC")
        resource = ET.SubElement(qrc, "qresource", prefix="/qtifw-trial")
        for name in support_files:
            ET.SubElement(resource, "file", alias=name).text = str(helper / name)
        ET.ElementTree(qrc).write(folder / "trial.qrc", encoding="utf-8", xml_declaration=True)
        config = folder / "config"
        program_meta = folder / "packages" / PACKAGE / "meta"
        for name, data in values.items():
            write(program_meta.parent / "data/program" / name, data)
        scripts = snapshot / "tools/qtifw_trial"
        # Render the real templates. Only names, shortcut directories, template
        # variables and an observational marker differ from product scripts.
        def unique(text):
            return text.replace("星图（试用版）", self.name).replace("星图试用版", self.name)
        component = unique((scripts / "component.js").read_text(encoding="utf-8"))
        write(program_meta / "component.js", component.replace("__PROGRAM_COPIES__", "[]"))
        write(program_meta / "install-location.ui", unique((scripts / "install-location.ui").read_text(encoding="utf-8")))
        component_version = "1.0.0" if version == "1.1.2" else "1.1.3"
        xml(program_meta / "package.xml", "Package", dict(DisplayName=self.name, Version=component_version,
            ReleaseDate="2026-11-13", Default="true", Script="component.js", Dependencies=DATA_PACKAGE))
        tree = ET.parse(program_meta / "package.xml")
        ET.SubElement(ET.SubElement(tree.getroot(), "UserInterfaces"), "UserInterface").text = "install-location.ui"
        tree.write(program_meta / "package.xml", encoding="utf-8", xml_declaration=True)
        data_meta = folder / "packages" / DATA_PACKAGE / "meta"
        xml(data_meta / "package.xml", "Package", dict(DisplayName="Synthetic cleanup", Version=component_version,
            ReleaseDate="2026-11-13", Virtual="true", Script="data-component.js"))
        write(data_meta / "data-component.js", (scripts / "data-component.js").read_text(encoding="utf-8")
              .replace("__SUPPORT_FILES__", json.dumps(support_files)))
        controller = unique((scripts / "controller.js").read_text(encoding="utf-8"))
        controller = controller.replace("__SCOPE__", '""').replace("__INSTANCE__", json.dumps(policy["instance"]))
        controller = controller.replace("__SUPPORT_FILES__", json.dumps(support_files))
        hook = "\n".join([
            "function Controller() {",
            "    console.log(" + json.dumps("NATIVE_UPGRADE_FIXTURE_CONTROLLER " + version + " " + policy["instance"]) + ");",
            "    installer.setValue(\"DesktopDir\", " + json.dumps(str(self.root / "desktop")) + ");",
            "    installer.setValue(\"UserStartMenuProgramsPath\", " + json.dumps(str(self.root / "programs")) + ");",
        ])
        write(config / "controller.js", controller.replace("function Controller() {", hook, 1))
        xml(config / "config.xml", "Installer", dict(Name=self.name, Version=version,
            Title="Synthetic QtIFW native upgrade fixture", Publisher="Independent synthetic regression",
            TargetDir=str(self.root / "targets/default-unused"), MaintenanceToolName="trial-maintenance",
            RemoveTargetDir="true", AllowNonAsciiCharacters="true", AllowSpaceInPath="true",
            AllowRepositoriesForOfflineInstaller="false", SaveDefaultRepositories="false",
            PersistentLocalCache="false", ControlScript="controller.js"))
        setup = folder / "synthetic-native-setup.exe"
        result = self.run("build-native-" + version, [self.ifw / "binarycreator.exe", "--offline-only",
            "--resources", folder / "trial.qrc", "-t", self.base, "-c", config / "config.xml",
            "-p", folder / "packages", setup])
        assert result["exitCode"] == 0, "Synthetic binarycreator failed"
        result = dict(folder=folder, helper=helper, setup=setup, policy=policy, values=values)
        self.report.setdefault("fixtures", {})[version] = dict(sourceCommit=source,
            instance=policy["instance"], policy=str(policy_path), helper=str(helper), setup=str(setup))
        self.save()
        return result

    def boot(self, fixture):
        folder, helper = fixture["folder"], fixture["helper"]
        boot = helper / "boot"
        boot.mkdir()
        shutil.copy2(fixture["setup"], boot / "native-setup.exe")
        shutil.copy2(self.base, boot / "installerbase.exe")
        shutil.copy2(folder / "config/controller.js", boot / "upgrade-controller.js")
        assert self.run("repogen-v113", [self.ifw / "repogen.exe", "-p", folder / "packages",
                                        boot / "repository"])["exitCode"] == 0
        assert self.run("update-rcc-v113", [self.ifw / "binarycreator.exe", "--resources", folder / "trial.qrc", "-c", folder / "config/config.xml",
            "-p", folder / "packages", "-rcc"], cwd=boot)["exitCode"] == 0
        assert (boot / "update.rcc").is_file()

    def install(self, label, old):
        target = self.root / "targets" / label
        # Separate names per case avoid old installation shortcut admission.
        # The previous case is fully purged before another install is created.
        self.targets.append(target)
        result = self.run(label + "-install-v112", [old["setup"], "--verbose", "--reject-messages",
            "--confirm-command", "install", "TrialTarget=" + str(target)])
        assert result["exitCode"] == 0, "Synthetic old native install failed"
        for name, data in old["values"].items():
            assert (target / "program" / name).read_bytes() == data, name
        assert versions(target) == {PACKAGE: "1.0.0", DATA_PACKAGE: "1.0.0"}
        owner = json.loads((target / OWNER).read_bytes())
        assert owner["phase"] == "complete" and owner["target"] == str(target)
        sentinels = {
            "program/work/ui-settings.json": b'{"newUserSetting":true,"automaticFailureEvidence":false}\n',
            "program/work/author-snapshots/activation.json": b"user active author snapshot\n",
            "program/work/map-packages/active-package.json": b"active package user state\n",
            "program/assets/custom-maps/route.json": b"user author route\n",
            "program/_internal/references/user-resource.bin": b"active user resource\n",
            "program/unknown.txt": b"unknown program file\n",
            "outside-user-work.txt": b"unknown install-root file\n",
        }
        for name, data in sentinels.items():
            write(target / name, data)
        return target, sentinels

    def post_extract_failure_repository(self, fixture):
        folder = self.root / "post-extract-failure-fixture"
        packages = folder / "packages"
        shutil.copytree(fixture["folder"] / "packages", packages)
        script = packages / PACKAGE / "meta/component.js"
        text = script.read_text(encoding="utf-8")
        verification = 'component.addOperation("Execute", "{0}", helper, "verify-program", "@TargetDir@");'
        assert text.count(verification) == 1, "Real component verification operation unavailable"
        # Change only the copied fixture metadata. The production helper keeps
        # faultsAllowed=false and will reject this additional Execute with 2.
        text = text.replace(verification, verification + '\n    component.addOperation("Execute", "{0}", helper, "fail", "@TargetDir@");', 1)
        write(script, text)
        repository = folder / "repository"
        result = self.run("repogen-post-extract-failure", [self.ifw / "repogen.exe", "-p", packages, repository])
        assert result["exitCode"] == 0
        return repository

    def assert_post_extract_failure(self, backup):
        logs = list(backup.glob("native-*/native-update.log"))
        assert len(logs) == 1, "Expected one failed native session before retry"
        data = logs[0].read_bytes()
        offsets = dict(
            oldComponentUndo=data.find(b'Removing files extracted from "1.0.0program.7z"'),
            newComponentExtract=data.find(b'Extracting "1.1.3program.7z"'),
            newProgramVerified=data.find(b'{"verified": true'),
            injectedExecuteRejected=data.find(b'fault-injection-disabled'),
        )
        assert all(value >= 0 for value in offsets.values()), offsets
        assert list(offsets.values()) == sorted(offsets.values()), offsets
        assert b'arguments: "fail ' in data, "Native engine did not execute injected helper fail operation"
        return dict(nativeLog=str(logs[0]), observedOrderOffsets=offsets,
                    boundary="after old UNDO, new Extract and successful verify-program")

    def assert_upgraded(self, target, new, sentinels, label):
        for name, data in new["values"].items():
            assert (target / "program" / name).read_bytes() == data, name
        for name, data in sentinels.items():
            assert (target / name).read_bytes() == data, name
        assert not (target / "program/obsolete-owned-v1.txt").exists()
        assert not (target / PENDING).exists()
        assert versions(target) == {PACKAGE: "1.1.3", DATA_PACKAGE: "1.1.3"}
        owner = json.loads((target / OWNER).read_bytes())
        assert owner == dict(instance=new["policy"]["instance"], payloadSource=new["policy"]["payloadSource"],
                             phase="complete", target=str(target))
        installed_policy = json.loads((target / "_installer-support/_internal/qtifw-trial-policy.json").read_bytes())
        assert installed_policy == new["policy"]
        assert (new["helper"] / "boot/installerbase.exe").is_file()
        assert (new["helper"] / "boot/update.rcc").is_file()
        result = self.run(label + "-next-maintenance-list", [target / "trial-maintenance.exe", "--verbose", "list"])
        assert result["exitCode"] == 0, "New maintenance failed without external --script"
        log = Path(result["log"]).read_bytes()
        assert ("NATIVE_UPGRADE_FIXTURE_CONTROLLER 1.1.3 " + new["policy"]["instance"]).encode() in log
        assert (b"TRIAL_inspect-data:" in log or b"TRIAL_hold-uninstall:" in log)
        assert b"TRIAL_select-data:" in log
        backups = list((target / "recovery-files").glob("*/upgrade-result.json"))
        assert len(backups) == 1
        backup = backups[0].parent
        for name, data in self.old["values"].items():
            assert (backup / "program" / name).read_bytes() == data, "old backup " + name
        self.report["cases"][label] = dict(success=True, nativePayloadVerified=True,
            userSentinels=len(sentinels), owner=owner, components=versions(target),
            nextMaintenanceExitCode=result["exitCode"], newControllerLoaded=True,
            oldProgramBackup=str(backup), userDataInProgramInventory=False)
        self.save()
        return backup

    def cleanup(self, target, label):
        # Keep the old program, native metadata, failed-native logs and receipts
        # outside the installation before its authorized full purge.
        evidence = self.root / "preserved" / label
        evidence.mkdir(parents=True, exist_ok=True)
        if (target / "recovery-files").exists():
            shutil.copytree(target / "recovery-files", evidence / "recovery-files", dirs_exist_ok=True)
        for name in (OWNER, "components.xml", PENDING):
            if (target / name).is_file():
                write(evidence / name, (target / name).read_bytes())
        assert target.parent == self.root / "targets" and target.is_relative_to(self.root)
        if (target / "trial-maintenance.exe").exists():
            result = self.run(label + "-purge-owned-fixture", [target / "trial-maintenance.exe",
                "--reject-messages", "--confirm-command", "purge", "DataChoice=clear"])
            self.report.setdefault("cleanup", {})[label] = dict(exitCode=result["exitCode"])
            self.report["cleanup"][label]["preservedEvidence"] = str(evidence)
            assert result["exitCode"] == 0, "Owned fixture cleanup failed"
            deadline = time.monotonic() + 15
            while target.exists() and time.monotonic() < deadline:
                time.sleep(.1)
            assert not target.exists(), "Deferred fixture target cleanup incomplete"
            self.report["cleanup"][label]["targetRemoved"] = True
            self.save()

    def execute(self):
        snapshot = self.source_snapshot()
        self.old = self.fixture("1.1.2", snapshot)
        new = self.fixture("1.1.3", snapshot)
        self.boot(new)
        labels = ["success"] if self.args.skip_failure else ["success", "repository-failure-retry", "post-extract-failure-retry"]
        for label in labels:
            target, sentinels = self.install(label, self.old)
            try:
                helper = new["helper"] / "qtifw-trial-helper.exe"
                checked = self.run(label + "-bootstrap-check", [helper, "bootstrap-check", target])
                assert checked["exitCode"] == 10
                if label != "success":
                    repository = new["helper"] / "boot/repository"
                    unavailable = repository.with_name("repository-preserved-during-outage")
                    repository.rename(unavailable)
                    faulty = None
                    if label == "post-extract-failure-retry":
                        faulty = self.post_extract_failure_repository(new)
                        faulty.rename(repository)
                    try:
                        failed = self.run(label + "-native-outage", [helper, "bootstrap-run", target])
                    finally:
                        if faulty is not None:
                            repository.rename(faulty)
                        unavailable.rename(repository)
                    assert failed["exitCode"] != 0
                    pending = json.loads((target / PENDING).read_bytes())
                    assert pending["stage"] == "native-in-progress"
                    backup = target / pending["backup"]
                    post_extract_evidence = self.assert_post_extract_failure(backup) if faulty else None
                    for name, data in sentinels.items():
                        assert (target / name).read_bytes() == data, "sentinel after native failure: " + name
                    for name, data in self.old["values"].items():
                        assert (backup / "program" / name).read_bytes() == data, "old backup after native failure: " + name
                    before = {p.relative_to(backup).as_posix(): sha(p.read_bytes())
                              for p in backup.rglob("*") if p.is_file()}
                    assert before and (backup / "program/星图.exe").read_bytes() == self.old["values"]["星图.exe"]
                    blocked = self.run(label + "-old-helper-ready-blocked", [
                        self.old["helper"] / "qtifw-trial-helper.exe", "inspect-data", target])
                    assert blocked["exitCode"] != 0
                    assert b"installer-upgrade-recovery-required" in Path(blocked["log"]).read_bytes()
                    ready_code = '''import json,sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from resource_transaction import resource_write_lock, assert_resources_ready
try:
    with resource_write_lock(Path(sys.argv[3])):
        assert_resources_ready(Path(sys.argv[3]))
except ValueError as error:
    print(json.dumps({"blocked": str(error)}))
    raise SystemExit(2)
raise SystemExit("Existing ready path unexpectedly admitted pending upgrade")
'''
                    legacy = self.run(label + "-existing-ready-blocked", [sys.executable, "-c", ready_code,
                        snapshot / "existing-ready", snapshot / "app", target / "program"])
                    assert legacy["exitCode"] == 2
                    assert b"installer-upgrade-recovery-required" in Path(legacy["log"]).read_bytes()
                upgraded = self.run(label + "-bootstrap-run", [helper, "bootstrap-run", target])
                assert upgraded["exitCode"] == 0, "Native bootstrap upgrade failed"
                backup = self.assert_upgraded(target, new, sentinels, label)
                if label != "success":
                    assert target / pending["backup"] == backup
                    after = {p.relative_to(backup).as_posix(): sha(p.read_bytes())
                             for p in backup.rglob("*") if p.is_file()}
                    assert all(after.get(name) == digest for name, digest in before.items())
                    self.report["cases"][label].update(nativeSourceOutageExit=failed["exitCode"],
                        previousHelperReadyBlocked=True, originalBackupRetainedAcrossRetry=True,
                        existingCommittedReadyPathBlocked=True,
                        userSentinelsRetainedImmediatelyAfterFailure=True,
                        failureBoundary=(post_extract_evidence["boundary"] if post_extract_evidence
                                         else "repository unavailable before payload extraction"))
                    if post_extract_evidence:
                        self.report["cases"][label]["postExtractFailureEvidence"] = post_extract_evidence
                self.save()
            finally:
                if not (target / PENDING).exists():
                    self.cleanup(target, label)
                else:
                    self.report.setdefault("cleanup", {})[label] = dict(
                        deferred="Unresolved native failure retained with pending and backup for diagnosis")
                    self.save()
        self.report["completed"] = True
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifw-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "work" / ("qtifw-native-upgrade-20261113-" + uuid.uuid4().hex[:10]))
    parser.add_argument("--skip-failure", action="store_true", help="Only native success, preservation and next-maintenance case")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows native fixture only")
    probe = NativeUpgrade(args)
    try:
        probe.execute()
    except BaseException:
        probe.report["failure"] = traceback.format_exc()
        probe.save()
        raise
    finally:
        print("RESULT " + str(probe.root / "result.json"), flush=True)


if __name__ == "__main__":
    main()
