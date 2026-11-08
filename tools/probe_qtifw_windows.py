"""Windows continuation of the cloud synthetic QtIFW probe; never a product installer.

Only newly created targets below a fresh output root are accepted. Inno fixtures
have independent random AppIds. No production discovery/uninstaller is invoked.
Recovery uses the stock maintenance tool, not a custom backup/rollback engine.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from probe_qtifw_lifecycle import PACKAGE, Probe, prepare_packages, snapshot


def scoped_target(root, target):
    """Reject traversal, aliases and reparse points before accepting a fixture path."""
    root, target = Path(root).absolute(), Path(target).absolute()
    if ".." in target.parts or target == root or not target.is_relative_to(root):
        raise ValueError("Target must be strictly inside the synthetic target root")
    for path in (target, *target.parents):
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse points are not synthetic targets")
    return target


DATA = {"settings.json": b'{"synthetic":true}\n', "user-created.txt": b"Synthetic retained user data\n"}


def apply_fixture_data_choice(root, target, choice, *, installation_succeeded):
    """Explicit, limited fixture adapter. This is NOT Xingtu user-data cleanup."""
    target = scoped_target(root, target)
    if choice not in {"keep", "clear"}:
        raise ValueError("An explicit keep/clear choice is required")
    if not installation_succeeded:
        raise ValueError("Data choice cannot run after a failed installation")
    work = scoped_target(root, target / "work")
    if choice == "keep":
        return
    files = list(work.rglob("*")) if work.exists() else []
    # Preflight all entries before deleting any; unknown files require a real
    # product retention contract, which this synthetic adapter does not invent.
    for path in files:
        scoped_target(root, path)
        if not path.is_file() or path.name not in DATA or path.parent != work:
            raise ValueError("Unknown data: fixture cleanup refused")
        if path.read_bytes() != DATA[path.name] or path.stat().st_nlink != 1:
            raise ValueError("Modified/linked data: fixture cleanup refused")
    for path in files:
        path.unlink()
    if work.exists():
        work.rmdir()


class WindowsProbe(Probe):
    def __init__(self, binaries, output, inno):
        scoped_target(Path(output).absolute().parent, output)
        super().__init__(binaries, output)
        self.env = dict(os.environ)
        self.inno = inno.resolve(strict=True)
        self.targets = self.root / "targets"
        self.targets.mkdir()
        self.identity = uuid.uuid4().hex
        self.report = {"platform": sys.platform, "productPackaged": False,
                       "productionInstallationTouched": False, "automaticRollbackImplemented": False,
                       "guiDataChoiceImplemented": False, "powerLossValidated": False}

    def target(self, name):
        return scoped_target(self.targets, self.targets / name)

    def build(self, label, version, *, fault=None, allowed_target=None):
        folder = prepare_packages(self.root / label, version, fault=fault)
        cfg = folder / "config/config.xml"
        tree = ET.parse(cfg)
        tree.getroot().find("Name").text += " " + self.identity
        tree.getroot().find("TargetDir").text = str(allowed_target or self.target("lifecycle 中文"))
        tree.write(cfg, encoding="utf-8", xml_declaration=True)
        script = folder / "packages" / PACKAGE / "meta/installscript.js"
        code = script.read_text(encoding="utf-8")
        # A bounded child exits itself even if the parent installer is stopped.
        code = code.replace("time.sleep(180)", "time.sleep(12)")
        prefix = self.targets.as_posix().casefold() + "/"
        check = ('    var normalized = target.replace(/\\\\/g, "/").toLowerCase();\n'
                 '    if (normalized.indexOf(' + json.dumps(prefix) + ') !== 0 || '
                 'normalized.indexOf("/../") >= 0) throw new Error("outside-synthetic-target-root");\n')
        code = code.replace('    var target = installer.value("TargetDir");\n',
                            '    var target = installer.value("TargetDir");\n' + check)
        script.write_text(code, encoding="utf-8")
        binary = folder / "synthetic-installer.exe"
        result = self.run(label + "-build", [self.binaries / "binarycreator.exe", "--offline-only",
            "-t", self.binaries / "installerbase.exe", "-c", cfg, "-p", folder / "packages", binary])
        if result.returncode:
            raise RuntimeError("Build failed: " + label)
        return folder, binary

    def install(self, label, binary, target, *, accept_nonempty_fixture=False):
        scoped_target(self.targets, target)
        return super().install(label, binary, target, accept_nonempty_fixture=accept_nonempty_fixture)

    def maintenance(self, target):
        return scoped_target(self.targets, target / "probe-maintenance.exe")

    def update(self, label, folder, target, *, repair=False):
        repository = folder / "repository"
        if not repository.exists():
            result = self.run(label + "-repo", [self.binaries / "repogen.exe", "-p", folder / "packages", repository])
            if result.returncode:
                raise RuntimeError("Repository generation failed")
        command = ["install", PACKAGE] if repair else ["update"]
        return self.run(label, [self.maintenance(target), "--set-temp-repository", repository.as_uri(),
            "--reject-messages", "--auto-answer", "installationErrorWithRetry=Cancel,installationError=Ok",
            "--confirm-command", *command])

    def verify_payload(self, target, folder):
        expected = snapshot(folder / "packages" / PACKAGE / "data")
        actual = snapshot(target)
        assert all(actual.get(name) == digest for name, digest in expected.items()), "Payload byte mismatch"
        if "program/obsolete-v1.txt" not in expected:
            assert "program/obsolete-v1.txt" not in actual, "Obsolete component file survived update"
        result = self.run("list-" + target.name + "-" + folder.name, [self.maintenance(target), "list"])
        version = ET.parse(folder / "packages" / PACKAGE / "meta/package.xml").findtext("Version")
        assert result.returncode == 0 and PACKAGE in result.stdout and version in result.stdout
        return {"payloadBytesVerified": True, "maintenanceListVerified": True, "version": version}

    def seed_data(self, target):
        work = scoped_target(self.targets, target / "work")
        work.mkdir()
        for name, value in DATA.items():
            (work / name).write_bytes(value)
        (target / "unknown-personal.txt").write_bytes(b"Unknown synthetic file, never installer owned.\n")
        return snapshot(work)

    def purge(self, label, target):
        result = self.run(label, [self.maintenance(target), "--reject-messages", "--confirm-command", "purge"])
        assert result.returncode == 0 and not (target / "program/version.txt").exists()

    def inno_fixture(self, name, target):
        folder = self.root / name
        folder.mkdir()
        fixture_id = "{" + str(uuid.uuid4()).upper() + "}"
        (folder / "legacy.txt").write_bytes(b"Synthetic Inno v0, not Xingtu.\n")
        script = '\n'.join([
            "[Setup]", "AppId=" + "{" + fixture_id,
            "AppName=Xingtu QtIFW Inno Fixture " + self.identity + name,
            "AppVersion=0.1.0", "DefaultDirName=" + str(target),
            "PrivilegesRequired=lowest", "Uninstallable=yes", "CreateAppDir=yes",
            "DisableProgramGroupPage=yes", "DisableDirPage=yes", "UsePreviousAppDir=no",
            "CloseApplications=no", "RestartApplications=no", "UninstallDisplayIcon=",
            "OutputDir=" + str(folder), "OutputBaseFilename=inno-fixture", "Compression=lzma2",
            "[Files]", 'Source: "' + str(folder / "legacy.txt") + '"; DestDir: "{app}\\program"', ""])
        iss = folder / "fixture.iss"
        iss.write_text(script, encoding="utf-8-sig")
        result = self.run(name + "-compile", [self.inno, iss])
        assert result.returncode == 0
        setup = folder / "inno-fixture.exe"
        result = self.run(name + "-install", [setup, "/CURRENTUSER", "/VERYSILENT", "/SUPPRESSMSGBOXES",
                                               "/NORESTART", "/DIR=" + str(target)])
        assert result.returncode == 0
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall" + "\\" + fixture_id + "_is1"
        entry = None
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | view) as handle:
                    entry = {k: winreg.QueryValueEx(handle, k)[0] for k in
                             ("DisplayName", "InstallLocation", "UninstallString")}
                break
            except FileNotFoundError:
                pass
        assert entry and entry["DisplayName"].startswith("Xingtu QtIFW Inno Fixture " + self.identity)
        assert Path(entry["InstallLocation"]).resolve() == target.resolve()
        uninstaller = scoped_target(self.targets, Path(entry["UninstallString"].strip('"')))
        assert uninstaller.name == "unins000.exe" and uninstaller.is_file()
        return setup, uninstaller, entry

    def migrate(self, name, binary, *, choice, new_directory=False, expect_success=True):
        old = self.target(name + " old")
        setup, uninstaller, entry = self.inno_fixture(name, old)
        data = self.seed_data(old)
        new = self.target(name + " new 中文") if new_directory else old
        # Refuse a destination collision before invoking the old uninstaller.
        if new != old:
            assert not new.exists()
        result = self.run(name + "-legacy-uninstall", [uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        assert result.returncode == 0 and not (old / "program/legacy.txt").exists()
        # Inno's self-delete helper can outlive the initial uninstaller process.
        # Do not disable the legacy guard or delete the residual files ourselves.
        deadline = time.monotonic() + 15
        while any((old / filename).exists() for filename in ("unins000.exe", "unins000.dat")):
            if time.monotonic() >= deadline:
                raise RuntimeError("Legacy fixture uninstaller has not completed self-removal")
            time.sleep(0.1)
        assert snapshot(old / "work") == data
        result = self.install(name + "-qt-install", binary, new, accept_nonempty_fixture=new == old)
        if not expect_success:
            assert result.returncode != 0 and snapshot(old / "work") == data
            # Known legacy installer, explicit forward repair; not UNDO/rollback.
            repair = self.run(name + "-legacy-reinstall", [setup, "/CURRENTUSER", "/VERYSILENT",
                "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=" + str(old)])
            assert repair.returncode == 0 and (old / "program/legacy.txt").read_bytes() == b"Synthetic Inno v0, not Xingtu.\n"
            assert snapshot(old / "work") == data
            return {"legacyDetected": True, "qtInstallExit": result.returncode,
                    "dataPreservedOnFailure": True, "explicitLegacyReinstallRecovered": True}
        assert result.returncode == 0
        verified = self.verify_payload(new, binary.parent)
        if new != old:
            # Copy only the explicitly seeded fixture files; retain the old copy.
            (new / "work").mkdir()
            for filename, value in DATA.items():
                (new / "work" / filename).write_bytes((old / "work" / filename).read_bytes())
        assert snapshot(new / "work") == data
        apply_fixture_data_choice(self.targets, new, choice, installation_succeeded=True)
        assert snapshot(new / "work") == (data if choice == "keep" else {})
        assert (old / "unknown-personal.txt").is_file()
        self.purge(name + "-purge", new)
        return {"legacyDetected": True, "legacyPathBoundToFixture": True,
                "newDirectory": new_directory, "dataChoice": choice,
                "dataChoiceVerified": True, "unknownPreserved": True,
                "oldDataCopyRetained": new_directory, "installedPayload": verified}

    def execute(self, *, skip_lifecycle=False):
        try:
            version = self.run("engine-version", [self.binaries / "installerbase.exe", "--version"])
            self.report["engineVersionOutput"] = version.stdout.strip()
            first_folder, first = self.build("v1", "1.0.0")
            second, _ = self.build("v2", "2.0.0")
            failing, bad_binary = self.build("failure", "3.0.0", fault="fail")
            _, pausing = self.build("pause", "3.0.0", fault="pause")
            _, gui = self.build("gui", "1.0.0", allowed_target=self.target("gui-cancel"))
            self.report["guiInstaller"] = str(gui)
            self.report["lifecycleSkippedUsingPriorEvidence"] = skip_lifecycle
            if not skip_lifecycle:
                target = self.target("lifecycle 中文")
                assert self.install("fresh-install", first, target).returncode == 0
                self.report["install"] = self.verify_payload(target, first_folder)
                data = self.seed_data(target)
                assert self.update("successful-update", second, target).returncode == 0
                self.report["update"] = self.verify_payload(target, second)
                assert snapshot(target / "work") == data
                result = self.update("failed-update", failing, target)
                assert result.returncode != 0
                self.report["failedUpdate"] = {"exitCode": result.returncode,
                    "files": snapshot(target), "oldPayloadAutomaticallyRestored":
                    snapshot(target / "program") == snapshot(second / "packages" / PACKAGE / "data/program")}
                assert self.update("explicit-repair", second, target, repair=True).returncode == 0
                self.report["repair"] = self.verify_payload(target, second)
                assert snapshot(target / "work") == data
                self.purge("uninstall-keep", target)
                assert snapshot(target / "work") == data and (target / "unknown-personal.txt").is_file()
                self.report["uninstallKeep"] = True
                clear = self.target("uninstall-clear")
                assert self.install("clear-install", first, clear).returncode == 0
                self.seed_data(clear)
                self.purge("uninstall-before-explicit-clear", clear)
                apply_fixture_data_choice(self.targets, clear, "clear", installation_succeeded=True)
                assert not (clear / "work").exists() and (clear / "unknown-personal.txt").is_file()
                self.report["uninstallExplicitFixtureClear"] = True
            self.report["migrationSameKeep"] = self.migrate("migration-keep", first, choice="keep")
            self.report["migrationSameClear"] = self.migrate("migration-clear", first, choice="clear")
            self.report["migrationNewDirectory"] = self.migrate("migration-new", first, choice="keep", new_directory=True)
            self.report["migrationFailure"] = self.migrate("migration-fail", bad_binary, choice="clear", expect_success=False)
            interrupted = self.target("interrupted")
            with (self.root / "interrupt.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(pausing), "--root", str(interrupted), "--reject-messages",
                    "--confirm-command", "install"], stdout=log, stderr=subprocess.STDOUT, env=self.env, cwd=self.root)
                checkpoint = interrupted / "probe-interrupt-checkpoint.txt"
                deadline = time.monotonic() + 45
                try:
                    while not checkpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    assert checkpoint.exists(), "Interruption checkpoint not reached"
                finally:
                    if process.poll() is None:
                        process.terminate()  # Only this created synthetic installer PID.
                    process.wait(timeout=15)
                # Its synthetic Execute child has a bounded 12-second lifetime.
                time.sleep(13)
            self.report["hardInterruptedInstall"] = {"exitCode": process.returncode,
                "payloadPresent": (interrupted / "program/version.txt").exists(),
                "maintenancePresent": self.maintenance(interrupted).exists(), "files": snapshot(interrupted)}
            retry = self.install("interrupt-retry", first, interrupted)
            self.report["interruptRetry"] = {"exitCode": retry.returncode,
                "automaticRecoveryClaimed": False, "residuePreserved": True}
            self.report["probeCompleted"] = True
        finally:
            self.report["runs"] = self.runs
            (self.root / "windows-result.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifw-bin", type=Path, required=True)
    parser.add_argument("--inno-compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-lifecycle", action="store_true", help="Run migration/interruption only; explicitly reuse earlier lifecycle evidence")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows only; use probe_qtifw_lifecycle.py for Linux")
    print(json.dumps(WindowsProbe(args.ifw_bin, args.output, args.inno_compiler).execute(skip_lifecycle=args.skip_lifecycle), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
