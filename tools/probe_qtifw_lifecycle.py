"""Exercise the unmodified QtIFW engine using only new, synthetic test roots.

This does not package Xingtu, migrate Inno, or invoke an existing installation.
No backup/rollback engine is implemented here: actual QtIFW outcomes are recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PACKAGE = "org.xingtu.qtifw.lifecycleprobe"


def write_xml(path, name, fields):
    root = ET.Element(name)
    for key, value in fields.items():
        ET.SubElement(root, key).text = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def prepare_packages(directory, version, *, fault=None):
    """Generate only synthetic payload; never take a product/user directory."""
    if directory.exists():
        raise ValueError("Package output must be new")
    if version not in {"1.0.0", "2.0.0", "3.0.0"}:
        raise ValueError("Only explicit synthetic fixture versions are accepted")
    if fault not in {None, "fail", "pause"}:
        raise ValueError("Unknown fixture fault")
    metadata = directory / "packages" / PACKAGE / "meta"
    data = metadata.parent / "data" / "program"
    data.mkdir(parents=True)
    (data / "version.txt").write_text(version + "\n", encoding="utf-8")
    (data / "synthetic.txt").write_text("Not the Xingtu application.\n", encoding="utf-8")
    if version == "1.0.0":
        (data / "obsolete-v1.txt").write_text("Removed by component update.\n")
    write_xml(metadata / "package.xml", "Package", {
        "DisplayName": "Synthetic application component",
        "Description": "QtIFW lifecycle probe, no product or user data",
        "Version": version, "ReleaseDate": "2026-11-08",
        "Default": "true", "Script": "installscript.js",
    })
    # This guard is a rejection, NOT an implementation of Inno migration.
    script = '''function Component() {}
Component.prototype.createOperations = function() {
    var target = installer.value("TargetDir");
    var legacy = ["unins000.exe", "unins000.dat", "星图.exe", "_internal/installation-profile.json"];
    for (var i = 0; i < legacy.length; ++i) {
        if (installer.fileExists(target + "/" + legacy[i]))
            throw new Error("legacy-inno-migration-required");
    }
    if (installer.isInstaller()) {
        var payload = ["program/version.txt", "program/synthetic.txt", "program/obsolete-v1.txt"];
        for (var j = 0; j < payload.length; ++j) {
            if (installer.fileExists(target + "/" + payload[j]))
                throw new Error("unowned-payload-collision");
        }
    }
    component.createOperations();
'''
    if fault:
        source = ("import sys; sys.exit(47)" if fault == "fail" else
                  "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                  "p.write_text('entered', encoding='utf-8'); time.sleep(180)")
        args = ["Execute", "{0}", sys.executable, "-c", source]
        if fault == "pause":
            args.append("@TargetDir@/probe-interrupt-checkpoint.txt")
        script += "    component.addOperation(" + ", ".join(json.dumps(a) for a in args) + ");\n"
    script += "};\n"
    (metadata / "installscript.js").write_text(script, encoding="utf-8")
    write_xml(directory / "config/config.xml", "Installer", {
        "Name": "Xingtu QtIFW Synthetic Probe", "Version": version,
        "Title": "Synthetic lifecycle test, not Xingtu Setup",
        "Publisher": "Xingtu development probe",
        "TargetDir": "@HomeDir@/Xingtu-QtIFW-Synthetic-Probe",
        "MaintenanceToolName": "probe-maintenance",
        "RemoveTargetDir": "false", "AllowNonAsciiCharacters": "true",
        "AllowSpaceInPath": "true", "AllowRepositoriesForOfflineInstaller": "false",
        "SaveDefaultRepositories": "false", "PersistentLocalCache": "false",
    })
    return directory


def snapshot(root):
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


class Probe:
    def __init__(self, binaries, output):
        self.binaries = binaries.resolve(strict=True)
        self.root = output.absolute()
        if self.root.exists() or self.root.is_symlink():
            raise ValueError("Probe requires a new output directory")
        self.root.mkdir(parents=True)
        (self.root / ".synthetic-qtifw-probe").write_text("v1\n")
        self.env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                        HOME=str(self.root / "home"), XDG_CONFIG_HOME=str(self.root / "config"),
                        XDG_CACHE_HOME=str(self.root / "cache"), XDG_DATA_HOME=str(self.root / "data"))
        (self.root / "home").mkdir()
        self.runs = []

    def run(self, label, command, *, input=None, timeout=90):
        started = time.monotonic()
        log_path = self.root / (label + ".log")
        with log_path.open("w", encoding="utf-8") as log:
            try:
                result = subprocess.run([str(p) for p in command], input=input,
                                        stdout=log, stderr=subprocess.STDOUT,
                                        text=True, env=self.env, cwd=self.root, timeout=timeout)
            except subprocess.TimeoutExpired:
                self.runs.append(dict(label=label, timedOut=True,
                    seconds=round(time.monotonic() - started, 3)))
                raise
        raw = log_path.read_bytes()
        try:
            result.stdout = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Windows native tools emit the active ANSI code page. Keep the
            # original log bytes, decode only the inspection copy.
            result.stdout = raw.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
        self.runs.append(dict(label=label, exitCode=result.returncode,
                              seconds=round(time.monotonic() - started, 3)))
        return result

    def build(self, label, version, *, fault=None):
        folder = prepare_packages(self.root / label, version, fault=fault)
        binary = folder / "synthetic-installer"
        result = self.run(label + "-build", [self.binaries / "binarycreator", "--offline-only",
            "-t", self.binaries / "installerbase", "-c", folder / "config/config.xml",
            "-p", folder / "packages", binary])
        if result.returncode:
            raise RuntimeError("QtIFW build failed: " + label)
        return folder, binary

    def install(self, label, binary, target, *, confirm=True, accept_nonempty_fixture=False):
        answers = "installationErrorWithRetry=Cancel,installationError=Ok"
        if accept_nonempty_fixture:
            answers += ",OverwriteTargetDirectory=Yes"
        options = ["--root", target, "--reject-messages", "--auto-answer",
                   answers]
        if confirm:
            options.append("--confirm-command")
        return self.run(label, [binary, *options, "install"], input=None if confirm else "n\n")

    def update(self, label, folder, target, *, repair=False):
        repository = folder / "repository"
        if not repository.exists():
            result = self.run(label + "-repo", [self.binaries / "repogen", "-p", folder / "packages", repository])
            if result.returncode:
                raise RuntimeError("QtIFW repository generation failed")
        command = ["install", PACKAGE] if repair else ["update"]
        return self.run(label, [target / "probe-maintenance", "--set-temp-repository",
            repository.as_uri(), "--reject-messages", "--auto-answer",
            "installationErrorWithRetry=Cancel,installationError=Ok", "--confirm-command", *command])

    def execute(self):
        report = {"schema": "xingtu-qtifw-synthetic-lifecycle-v1", "productPackaged": False,
                  "windowsValidated": False, "innoMigrationImplemented": False,
                  "userDataDeletionImplemented": False, "powerLossRecoveryValidated": False}
        try:
            version = self.run("engine-version", [self.binaries / "installerbase", "--version"])
            report["engineVersionOutput"] = version.stdout.strip()
            _, first = self.build("v1", "1.0.0")
            second, _ = self.build("v2", "2.0.0")
            failing, failing_binary = self.build("failure", "3.0.0", fault="fail")
            _, pausing = self.build("pause", "3.0.0", fault="pause")
            # External data are never a component, operation or runtime backup.
            user_data = self.root / "synthetic-user-data"
            user_data.mkdir()
            (user_data / "settings.json").write_text('{"synthetic":true}\n')
            baseline = snapshot(user_data)
            target = self.root / "install with spaces 中文"
            result = self.install("fresh-install", first, target)
            assert result.returncode == 0 and (target / "program/version.txt").read_text().strip() == "1.0.0"
            (target / "work").mkdir()
            (target / "work/user-created.txt").write_text("Synthetic retained user data\n")
            (target / "program/local-created.txt").write_text("Unknown file\n")
            result = self.update("successful-update", second, target)
            assert result.returncode == 0 and (target / "program/version.txt").read_text().strip() == "2.0.0"
            assert not (target / "program/obsolete-v1.txt").exists()
            assert (target / "work/user-created.txt").read_text() == "Synthetic retained user data\n"
            assert (target / "program/local-created.txt").read_text() == "Unknown file\n"
            result = self.update("failed-update", failing, target)
            assert result.returncode != 0
            version_file = target / "program/version.txt"
            report["failedUpdate"] = {"exitCode": result.returncode,
                "payloadVersionAfterFailure": version_file.read_text().strip() if version_file.exists() else None,
                "maintenancePresent": (target / "probe-maintenance").exists(),
                "files": snapshot(target)}
            # Explicitly observe whether stock updater restores old payload.
            self.run("after-failed-update-list", [target / "probe-maintenance", "list"])
            result = self.update("repair-after-failed-update", second, target, repair=True)
            report["repairAfterFailedUpdate"] = {"exitCode": result.returncode,
                "payloadVersion": version_file.read_text().strip() if version_file.exists() else None}
            unknown = target / "unknown-personal.txt"
            unknown.write_text("Synthetic unknown data, not installer owned.\n")
            before = unknown.read_bytes()
            result = self.run("uninstall", [target / "probe-maintenance", "--reject-messages",
                "--confirm-command", "purge"])
            report["uninstall"] = {"exitCode": result.returncode,
                "ownedPayloadRemoved": not version_file.exists(),
                "unknownPreserved": unknown.exists() and unknown.read_bytes() == before,
                "workPreserved": (target / "work/user-created.txt").read_text() == "Synthetic retained user data\n",
                "unknownInProgramPreserved": (target / "program/local-created.txt").read_text() == "Unknown file\n"}
            cancelled = self.root / "cancelled"
            result = self.install("cancel-before-install", first, cancelled, confirm=False)
            report["cancel"] = {"exitCode": result.returncode, "payloadAbsent": not (cancelled / "program").exists()}
            fresh_failure = self.root / "fresh-failure"
            result = self.install("new-install-operation-failure", failing_binary, fresh_failure)
            report["freshFailure"] = {"exitCode": result.returncode,
                "payloadAbsent": not (fresh_failure / "program/version.txt").exists()}
            legacy = self.root / "legacy-synthetic"
            legacy.mkdir()
            (legacy / "unins000.exe").write_bytes(b"synthetic Inno sentinel; never executed")
            before = snapshot(legacy)
            result = self.install("inno-transition-rejected", first, legacy)
            report["legacyRejected"] = {"exitCode": result.returncode,
                "filesUnchanged": snapshot(legacy) == before}
            result = self.install("legacy-guard-after-nonempty-consent", first, legacy,
                                  accept_nonempty_fixture=True)
            report["legacyGuardAfterConsent"] = {"exitCode": result.returncode,
                "filesUnchanged": snapshot(legacy) == before,
                "guardObserved": "legacy-inno-migration-required" in result.stdout}
            collision = self.root / "unknown-collision"
            (collision / "program").mkdir(parents=True)
            (collision / "program/version.txt").write_text("Unknown existing file\n")
            before = snapshot(collision)
            result = self.install("unknown-collision-rejected", first, collision)
            report["unknownCollisionRejected"] = {"exitCode": result.returncode,
                "filesUnchanged": snapshot(collision) == before}
            result = self.install("collision-guard-after-nonempty-consent", first, collision,
                                  accept_nonempty_fixture=True)
            report["collisionGuardAfterConsent"] = {"exitCode": result.returncode,
                "filesUnchanged": snapshot(collision) == before,
                "guardObserved": "unowned-payload-collision" in result.stdout}
            # Kill only this explicitly created process group, after our owned
            # test operation signals. No name-wide kill and no user processes.
            interrupted = self.root / "interrupted"
            with (self.root / "interrupt.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(pausing), "--root", str(interrupted), "--reject-messages",
                    "--confirm-command", "install"], stdout=log, stderr=subprocess.STDOUT,
                    env=self.env, cwd=self.root, start_new_session=True)
                try:
                    limit = time.monotonic() + 45
                    checkpoint = interrupted / "probe-interrupt-checkpoint.txt"
                    while not checkpoint.exists() and process.poll() is None and time.monotonic() < limit:
                        time.sleep(0.05)
                    if not checkpoint.exists():
                        raise RuntimeError("Interrupt checkpoint was not reached")
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
            report["abruptTermination"] = {"exitCode": process.returncode,
                "payloadPresent": (interrupted / "program/version.txt").exists(),
                "maintenancePresent": (interrupted / "probe-maintenance").exists(),
                "files": snapshot(interrupted)}
            before = snapshot(interrupted)
            result = self.install("interrupt-retry-no-overwrite", first, interrupted)
            report["interruptRetry"] = {"exitCode": result.returncode,
                "filesUnchanged": snapshot(interrupted) == before,
                "automaticRecovery": False}
            report["externalUserDataPreserved"] = snapshot(user_data) == baseline
            report["probeCompleted"] = True
        finally:
            report["runs"] = self.runs
            (self.root / "result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifw-bin", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This process-group fault probe currently supports Linux only")
    print(json.dumps(Probe(args.ifw_bin, args.output).execute(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
