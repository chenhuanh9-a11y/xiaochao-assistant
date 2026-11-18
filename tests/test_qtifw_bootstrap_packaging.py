"""Compile native QtIFW support and run a synthetic UI; never install or write ARP."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_qtifw_trial as build
from probe_qtifw_lifecycle import write_xml
from qtifw_upgrade import materialize_repository


class QtIfwBootstrapPackagingTests(unittest.TestCase):
    def test_native_hidden_poll_button_keeps_real_qrc_preparation_and_child_wait_responsive(self):
        ifw = Path(os.environ.get("STARMAP_TEST_IFW_BIN", r"E:\codex\tools\qtifw-4.11.0\bin"))
        if not (ifw / "binarycreator.exe").is_file():
            self.skipTest("QtIFW 4.11 native compiler is unavailable")
        with tempfile.TemporaryDirectory(prefix="星图 native async UI ") as temporary:
            output = Path(temporary)
            config, meta = output / "config", output / "packages/fixture/meta"
            config.mkdir()
            meta.mkdir(parents=True)
            (output / "packages/fixture/data").mkdir()
            (output / "packages/fixture/data/never-installed.txt").write_text("never installed")
            support = output / "support"
            support.mkdir()
            worker = output / "finite-worker.py"
            worker.write_text("import sys,time\nfrom pathlib import Path\n"
                + "sys.path.insert(0," + repr(str(ROOT / "tools")) + ")\n"
                + "sys.path.insert(0," + repr(str(ROOT / "app")) + ")\n"
                + "from qtifw_update_job import publish\ntime.sleep(1)\n"
                + "publish({'instance':'fixture'},Path(sys.argv[2]),Path(sys.argv[1]),"
                + "{'success':True,'target':str(Path(sys.argv[2]).resolve())})\n", encoding="utf-8")
            ui = ET.parse(ROOT / "tools/qtifw_trial/existing-update.ui")
            pulse = ET.SubElement(ui.getroot().find("widget"), "widget", {"class": "QPushButton", "name": "AuditHeartbeat"})
            ET.SubElement(ET.SubElement(pulse, "property", name="visible"), "bool").text = "false"
            ui.write(meta / "existing-update.ui", encoding="utf-8", xml_declaration=True)
            files = ["boot/installerbase.exe", "boot/devtool.exe"]
            # Only helper process work is substituted by a finite real Python
            # child. Production component/page, native QRC Copy, hidden button,
            # clickButton timers, UI thread and result-file reads all run here.
            boundary = r'''
var realInstaller=installer;
var audit={clickMs:null,maxGapMs:0,beats:0,copies:[],executes:[],success:false};
var lastBeat=Date.now();
var auditCase=typeof realInstaller.environmentVariable==="function"?realInstaller.environmentVariable("STARMAP_UI_CASE"):"success";
var copyFault=auditCase==="copy-retry";
installer={};
["value","setValue","isInstaller","isCommandLineInstance","isUninstaller",
 "setMessageBoxAutomaticAnswer","environmentVariable","toNativeSeparators",
 "fileExists","readFile","addWizardPage","setDefaultPageVisible",
 "setValidatorForCustomPage"].forEach(function(name){
 installer[name]=function(){return realInstaller[name].apply(realInstaller,arguments);};
});
installer.performOperation=function(name,args){
 if(name==="Copy" && args[0].indexOf("devtool.exe")>=0 && copyFault){copyFault=false;audit.copyFailureInjected=true;return false;}
 var begin=Date.now();var result=realInstaller.performOperation(name,args);
 if(name==="Copy")audit.copies.push({file:args[0],elapsedMs:Date.now()-begin});
 return result;
};
installer.execute=function(program,args){
 if(args[0]==="bootstrap-start"){
  var ok=realInstaller.executeDetached(PYTHON,["-B",WORKER,args[3],args[1]]);
  return[JSON.stringify({started:ok,instance:"fixture",pid:4242,created:"fixture",
   target:args[1],resultFile:args[3]}),ok?0:2];
 }
 if(args[0]==="bootstrap-status")return['{"running":true}',0];
 var begin=Date.now();var result=realInstaller.execute(program,args);
 audit.executes.push({program:program,elapsedMs:Date.now()-begin});return result;
};
'''.replace("PYTHON", json.dumps(sys.executable)).replace("WORKER", json.dumps(str(worker)))
            production = (ROOT / "tools/qtifw_trial/component.js").read_text(encoding="utf-8").replace("__PROGRAM_COPIES__", "[]")
            audit_script = r'''
var originalClick=Component.prototype.updateExisting;
Component.prototype.updateExisting=function(){var begin=Date.now();lastBeat=begin;audit.maxGapMs=0;audit.beats=0;var r=originalClick.call(this);audit.clickMs=Date.now()-begin;return r;};
var OriginalComponent=Component;
Component=function(){OriginalComponent.call(this);
 this.auditPulse=function(){var now=Date.now();audit.maxGapMs=Math.max(audit.maxGapMs,now-lastBeat);lastBeat=now;++audit.beats;
  audit.success=installer.value("TrialHandoffSuccess","")==="true";
  if(auditCase==="copy-retry" && installer.value("TrialHandoffBusy","")!=="true" &&
     installer.value("TrialHandoffFailure","")!=="" && !audit.retried){
   audit.retried=true;gui.clickButton("UpdateButton",0);
  }
  if(auditCase==="close-running" && this.updateStage==="wait" && audit.beats>5){
   audit.closedWhileBusy=installer.value("TrialHandoffBusy","")==="true";audit.resultFile=this.updateResultFile;
   console.log("NATIVE_ASYNC_RESULT:"+JSON.stringify(audit));gui.clickButton("CloseButton",0);return;
  }
  if(audit.success || now-auditBegin>12000){
   audit.status=gui.pageWidgetByObjectName("DynamicExistingUpdate").UpdateStatus.text;
   console.log("NATIVE_ASYNC_RESULT:"+JSON.stringify(audit));gui.rejectWithoutPrompt();return;}
  gui.clickButton("AuditHeartbeat",20);};
 component.loaded.connect(this,function(){
  var page=gui.pageWidgetByObjectName("DynamicExistingUpdate");
  page.AuditHeartbeat.visible=false;
  page.AuditHeartbeat.clicked.connect(this,this.auditPulse);
  gui.clickButton("AuditHeartbeat",0);gui.clickButton("UpdateButton",50);
 });
};Component.prototype=OriginalComponent.prototype;
var auditBegin=Date.now();
'''
            (meta / "component.js").write_text(boundary + production + audit_script, encoding="utf-8")
            write_xml(meta / "package.xml", "Package", dict(DisplayName="Finite UI fixture", Version="1.0.0",
                ReleaseDate="2026-11-13", Default="true", Script="component.js"))
            tree = ET.parse(meta / "package.xml")
            ET.SubElement(ET.SubElement(tree.getroot(), "UserInterfaces"), "UserInterface").text = "existing-update.ui"
            tree.write(meta / "package.xml", encoding="utf-8", xml_declaration=True)
            controller = '''function Controller(){
installer.setValue("TrialNativeHandoff","true");installer.setValue("TrialInstallerInstance","fixture");
var support=SUPPORT+"/"+installer.environmentVariable("STARMAP_UI_CASE");
installer.performOperation("Mkdir",[support]);
installer.setValue("TrialSupport",support);installer.setValue("TrialHelper","unused-process-boundary");
installer.setValue("TrialNativeUpgradeFiles",FILES);
installer.setDefaultPageVisible(QInstaller.TargetDirectory,false);
installer.setDefaultPageVisible(QInstaller.ComponentSelection,false);
installer.setDefaultPageVisible(QInstaller.LicenseCheck,false);
installer.setDefaultPageVisible(QInstaller.StartMenuSelection,false);
}
Controller.prototype.IntroductionPageCallback=function(){gui.clickButton(buttons.NextButton);};
'''.replace("SUPPORT", json.dumps(str(support))).replace("FILES", json.dumps(json.dumps(files)))
            (config / "controller.js").write_text(controller, encoding="utf-8")
            target = output / "never-installed"
            write_xml(config / "config.xml", "Installer", dict(Name="Owned finite native UI fixture",
                Version="1.0.0", Title="Finite UI fixture", TargetDir=str(target),
                AllowNonAsciiCharacters="true", AllowSpaceInPath="true", ControlScript="controller.js"))
            qrc = ET.Element("RCC")
            entries = ET.SubElement(qrc, "qresource", prefix="/qtifw-upgrade")
            for name in files:
                ET.SubElement(entries, "file", alias=name).text = str(ifw / Path(name).name)
            ET.ElementTree(qrc).write(output / "support.qrc", encoding="utf-8", xml_declaration=True)
            setup = output / "finite-native-Setup.exe"
            compiled = subprocess.run([str(ifw / "binarycreator.exe"), "--offline-only", "--resources", str(output / "support.qrc"),
                "-c", str(config / "config.xml"), "-p", str(output / "packages"), str(setup)],
                capture_output=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, (compiled.stdout + compiled.stderr).decode("utf-8", errors="replace"))
            for case in ("success", "copy-retry", "close-running"):
                with self.subTest(case=case):
                    native = subprocess.run([str(setup), "--verbose"], capture_output=True, timeout=25,
                        env=dict(os.environ, QT_QPA_PLATFORM="windows", STARMAP_UI_CASE=case))
                    log = (native.stdout + native.stderr).decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")
                    reports = [line.split("NATIVE_ASYNC_RESULT:", 1)[1] for line in log.splitlines() if "NATIVE_ASYNC_RESULT:" in line]
                    self.assertTrue(reports, str(native.returncode) + ": " + log[-8000:])
                    report = json.loads(reports[-1])
                    report["case"] = case
                    if case == "close-running":
                        self.assertTrue(report["closedWhileBusy"], report)
                        self.assertFalse(report["success"], "Starting a child cannot claim an update succeeded")
                        result = Path(report["resultFile"])
                        import time
                        deadline = time.monotonic() + 10
                        while not result.exists() and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertTrue(json.loads(result.read_text(encoding="utf-8"))["success"])
                        report["childCompletedAfterClose"] = True
                    else:
                        self.assertTrue(report["success"], report)
                        self.assertGreater(report["beats"], 10, report)
                    self.assertLess(report["clickMs"], 100, report)
                    self.assertLess(report["maxGapMs"], 1000, report)
                    self.assertEqual(len(report["copies"]), 3 if case == "copy-retry" else 2, report)
                    self.assertFalse(target.exists(), "The UI fixture must never start a native installation")
                    report["copiedBytes"] = {name: (support / case / name).stat().st_size for name in files}
                    print("NATIVE_ASYNC_RESULT:" + json.dumps(report, ensure_ascii=True), flush=True)
                    evidence = os.environ.get("STARMAP_NATIVE_UI_EVIDENCE")
                    if evidence:
                        destination = Path(evidence)
                        destination.mkdir(parents=True, exist_ok=True)
                        name = "native-async-ui" + ("" if case == "success" else "-" + case)
                        (destination / (name + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        (destination / (name + ".log")).write_text(log, encoding="utf-8")

    def test_real_native_setup_contains_one_program_archive_and_rebuilds_offline_repository(self):
        ifw = Path(os.environ.get("STARMAP_TEST_IFW_BIN", r"E:\codex\tools\qtifw-4.11.0\bin"))
        if not (ifw / "binarycreator.exe").is_file():
            self.skipTest("QtIFW 4.11 native compiler is unavailable")
        with tempfile.TemporaryDirectory(prefix="星图 native QtIFW compile ") as temporary:
            output = Path(temporary)
            packages, config = output / "packages", output / "config"
            config.mkdir()
            payload = packages / build.PACKAGE / "data/program"
            payload.mkdir(parents=True)
            raw = b"synthetic program, never executed\n" * 2048
            (payload / "fixture.bin").write_bytes(raw)
            for name in (build.PACKAGE, build.DATA_PACKAGE):
                write_xml(packages / name / "meta/package.xml", "Package", dict(
                    DisplayName="Native compile fixture", Version="1.1.5", ReleaseDate="2026-11-13", Default="true"))
            (config / "controller.js").write_text("function Controller() {}\n", encoding="utf-8")
            write_xml(config / "config.xml", "Installer", dict(Name="Owned compile fixture", Version="1.1.5",
                Title="Compile only", Publisher="Synthetic", TargetDir=str(output / "never-installed"),
                ControlScript="controller.js"))
            record = build.build_upgrade_support(output, packages, config, ifw, compression=3)
            layout = record["archiveLayout"]
            self.assertEqual(len(layout), 1)
            self.assertFalse(payload.exists())
            self.assertTrue((packages / build.PACKAGE / "data/program.7z").is_file())
            self.assertFalse((output / "upgrade-support/boot/repository" / layout[0]["repository"]).exists())
            self.assertFalse((output / "upgrade-support/boot/native-setup.exe").exists())
            qrc = ET.Element("RCC")
            entries = ET.SubElement(qrc, "qresource", prefix="/qtifw-upgrade")
            for path in (output / "upgrade-support").rglob("*"):
                if path.is_file():
                    ET.SubElement(entries, "file", alias=path.relative_to(output / "upgrade-support").as_posix()).text = str(path)
            ET.ElementTree(qrc).write(output / "support.qrc", encoding="utf-8", xml_declaration=True)
            setup = output / "native-Setup.exe"
            base = build.administrator_installer_base(ifw, output)
            subprocess.run([str(ifw / "binarycreator.exe"), "--offline-only", "--compression", "3", "--resources",
                str(output / "support.qrc"), "-t", str(base), "-c", str(config / "config.xml"), "-p", str(packages), str(setup)],
                check=True, capture_output=True)
            build.build.verify_administrator_manifest(setup)
            result = materialize_repository(output / "upgrade-support", setup)
            self.assertEqual(result, dict(materialized=True, archives=1))
            archive = output / "upgrade-support/boot/repository" / layout[0]["repository"]
            self.assertEqual(archive.read_bytes(), (packages / build.PACKAGE / "data/program.7z").read_bytes())
            dumped = list((output / "upgrade-support/boot").glob("archive-dump-*/metadata/*/*program.7z"))
            self.assertEqual(len(dumped), 1)
            # Read the official native PE without launching it.
            import pefile
            with pefile.PE(str(setup)) as pe:
                native_code = next(section.get_data() for section in pe.sections if section.Name.rstrip(b"\0") == b".text")
            with pefile.PE(str(ifw / "installerbase.exe")) as pe:
                official_code = next(section.get_data() for section in pe.sections if section.Name.rstrip(b"\0") == b".text")
            self.assertEqual(native_code, official_code)
            self.assertNotIn(b"Inno Setup", setup.read_bytes())


if __name__ == "__main__":
    unittest.main()
