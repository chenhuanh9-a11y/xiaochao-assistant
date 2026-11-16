"""Native renamed-Setup arbitration; synthetic target, no installation or client."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
from probe_qtifw_lifecycle import write_xml
from resource_transaction import _safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ifw-bin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = _safe(args.output.absolute())
    if os.name != 'nt' or not root.is_relative_to(ROOT / 'work') or root == ROOT / 'work' or root.exists():
        parser.error('Requires Windows and a new owned checkout/work output directory')
    root.mkdir(parents=True)
    key = uuid.uuid4().hex
    policy = dict(instance=key, portable=False, files={}, faultsAllowed=False, localCacheDir='xingtu-instance-fixture-' + key)
    (root / 'qtifw-trial-policy.json').write_text(json.dumps(policy), encoding='utf-8')
    with (root / 'freeze.log').open('wb') as log:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console',
            '--name', 'qtifw-trial-helper', '--paths', str(ROOT / 'app'), '--paths', str(ROOT / 'tools'),
            '--exclude-module', 'PySide6', '--exclude-module', 'numpy', '--exclude-module', 'cv2', '--exclude-module', 'PIL',
            '--distpath', str(root / 'helper'), '--workpath', str(root / 'freeze-work'), '--specpath', str(root),
            '--add-data', str(root / 'qtifw-trial-policy.json') + ';.', str(ROOT / 'tools/qtifw_trial_helper.py')],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
    helper = root / 'helper/qtifw-trial-helper'
    files = sorted(p.relative_to(helper).as_posix() for p in helper.rglob('*') if p.is_file())
    qrc = ET.Element('RCC')
    resource = ET.SubElement(qrc, 'qresource', prefix='/qtifw-trial')
    for name in files:
        ET.SubElement(resource, 'file', alias=name).text = str(helper / name)
    ET.ElementTree(qrc).write(root / 'helper.qrc', encoding='utf-8')
    config = root / 'config'
    config.mkdir()
    script = (ROOT / 'tools/qtifw_trial/controller.js').read_text(encoding='utf-8')
    script = script.replace('__SCOPE__', '""').replace('__INSTANCE__', json.dumps(key))
    script = script.replace('__UPGRADE_FILES__', '[]').replace('__SUPPORT_FILES__', json.dumps(files))
    # Preserve the production constructor and duplicate handling. Only log its
    # observed result and schedule bounded cancellation of the owned fixture.
    script += '''
var RealController = Controller;
Controller = function() {
    if (installer.value("GuardDelay", "") === "true") {
        console.log("INSTANCE_CACHE_READY");
        installer.execute(installer.environmentVariable("SystemRoot") + "/System32/WindowsPowerShell/v1.0/powershell.exe",
            ["-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Milliseconds 2000"]);
    }
    RealController.call(this);
    console.log("INSTANCE_FIXTURE: " + installer.value("TrialDuplicateSetup", "false"));
    installer.setMessageBoxAutomaticAnswer("cancelInstallation", QMessageBox.Yes);
    if (installer.value("TrialDuplicateSetup", "false") !== "true") gui.clickButton(buttons.CancelButton, 20000);
};
Controller.prototype = RealController.prototype;
'''
    (config / 'controller.js').write_text(script, encoding='utf-8')
    meta = root / 'packages/org.xingtu.instancefixture/meta'
    data = meta.parent / 'data'
    data.mkdir(parents=True)
    (data / 'synthetic.txt').write_text('Never installed.\n')
    write_xml(meta / 'package.xml', 'Package', dict(DisplayName='Owned fixture', Version='1.0.0', Default='true', ReleaseDate='2026-11-15'))
    write_xml(config / 'config.xml', 'Installer', dict(Name='Xingtu owned instance fixture ' + key,
        Title='Owned instance fixture', Version='1.0.0', TargetDir=str(root / 'never-installed'),
        ControlScript='controller.js', LocalCacheDir='xingtu-instance-fixture-' + key,
        AllowNonAsciiCharacters='true', AllowSpaceInPath='true'))
    setup = root / 'first.exe'
    with (root / 'build.log').open('wb') as log:
        subprocess.run([str(args.ifw_bin / 'binarycreator.exe'), '--offline-only', '--resources', str(root / 'helper.qrc'),
            '-c', str(config / 'config.xml'), '-p', str(root / 'packages'), str(setup)],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    other = root / 'renamed-copy.exe'
    shutil.copy2(setup, other)
    results = []
    env = dict(os.environ, QT_QPA_PLATFORM='windows', TEMP=str(root), TMP=str(root))
    for simultaneous in (False, True, 'inverted'):
        handles = [open(root / (str(simultaneous) + '-' + str(i) + '.log'), 'wb') for i in range(2)]
        children = []
        try:
            for index, executable in enumerate((setup, other)):
                delay = ['GuardDelay=true'] if simultaneous == 'inverted' and index == 0 else []
                children.append(subprocess.Popen([str(executable), '--verbose', *delay, 'TrialTarget=' + str(root / 'never-installed')],
                    stdout=handles[index], stderr=subprocess.STDOUT, env=env))
                if (not simultaneous or simultaneous == 'inverted') and index == 0:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        marker = 'INSTANCE_CACHE_READY' if simultaneous == 'inverted' else 'INSTANCE_FIXTURE: false'
                        if marker in Path(handles[0].name).read_text(encoding='utf-8', errors='replace'):
                            break
                        time.sleep(.05)
            codes = [child.wait(timeout=35) for child in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()  # Failure cleanup of these owned fixtures only.
                    child.wait(timeout=10)
            for handle in handles:
                handle.close()
        logs = [Path(handle.name).read_text(encoding='utf-8', errors='replace') for handle in handles]
        primaries = sum('INSTANCE_FIXTURE: false' in log for log in logs)
        duplicates = sum('INSTANCE_FIXTURE: true' in log for log in logs)
        assert (primaries, duplicates) == (1, 1), (primaries, duplicates, codes)
        # A second SDK logs its initial cache warning before Controller. It
        # must exit before metadata fetching can display it as a page error.
        primary = next(log for log in logs if 'INSTANCE_FIXTURE: false' in log)
        duplicate = next(log for log in logs if 'INSTANCE_FIXTURE: true' in log)
        assert 'Temporary space required' in primary, 'Primary never passed native metadata loading'
        assert 'Temporary space required' not in duplicate, 'Duplicate advanced past arbitration'
        if simultaneous == 'inverted':
            assert 'INSTANCE_FIXTURE: false' in logs[0], 'Cache owner lost product arbitration'
        assert not (root / 'never-installed').exists()
        results.append(dict(simultaneous=simultaneous, primary=primaries, duplicate=duplicates, exitCodes=codes))
    (root / 'result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
