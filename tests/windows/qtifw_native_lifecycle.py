"""Native QtIFW lifecycle regression; never launches the synthetic EXE."""
from pathlib import Path
import argparse, hashlib, json, os, stat, subprocess, sys, time, uuid
import xml.etree.ElementTree as ET

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ifw-bin',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--baseline-ref',help='Reproduce the pre-fix counterexamples from this Git ref, not a passing-current run')
parser.add_argument('--portable',action='store_true',help='Exercise distribution mode without a build-directory dependency')
args=parser.parse_args()
if os.name!='nt':parser.error('Windows native fixture only')
ROOT=Path(__file__).resolve().parents[2]; IFW=args.ifw_bin.absolute()
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'app')]
from probe_qtifw_lifecycle import write_xml
PACKAGE='org.xingtu.qtifw.realtrial'; DATA='org.xingtu.qtifw.trialdata'
which='baseline' if args.baseline_ref else 'current'; runroot=args.output.absolute()
old=bool(args.baseline_ref)
if old and args.portable:parser.error('Historical baseline has no portable mode')
from resource_transaction import _safe
if '..' in runroot.parts or not runroot.is_relative_to(ROOT/'work') or runroot==ROOT/'work':
    parser.error('New fixture output must be inside this checkout work directory')
_safe(runroot)
runroot.mkdir(parents=True)
if args.portable:
    (runroot/'temp').mkdir()
    os.environ['TEMP']=os.environ['TMP']=str(runroot/'temp')
instance=uuid.uuid4().hex;(runroot/'targets').mkdir();(runroot/'.qtifw-trial-scope').write_text(instance)
values={'星图.exe':b'synthetic EXE - do not execute\n','_internal/runtime.txt':b'expected runtime\n'}
if args.portable:
    values.update({n:b'deduplicated native payload\n'*3000 for n in
                   ['_internal/canonical.bin','_internal/nested/alias-a.bin','runtime/alias-b.bin']})
    values['_internal/python313.dll']=(Path(sys.base_prefix)/'python313.dll').read_bytes()
policy=dict(scope=str(runroot),instance=instance,payloadSource='a'*40,allowNewTargets=True,fullUninstall=True,
            portable=args.portable,faultsAllowed=False,files={n:dict(bytes=len(b),sha256=hashlib.sha256(b).hexdigest()) for n,b in values.items()})
if args.portable:policy.pop('scope')
(runroot/'qtifw-trial-policy.json').write_text(json.dumps(policy),encoding='utf-8')

def source(name):
    return subprocess.check_output(['git','show',args.baseline_ref+':'+name],cwd=ROOT) if old else (ROOT/name).read_bytes()

def call(label,args,timeout=120):
    with (runroot/(label+'.log')).open('wb') as log:
        p=subprocess.run([str(a) for a in args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    return p.returncode

sources=runroot/'sources';sources.mkdir()
for name in ['tools/qtifw_trial_helper.py','tools/qtifw_trial_lease.py']:
    if old and name.endswith('_lease.py'):continue
    (sources/Path(name).name).write_bytes(source(name))
code=call('freeze',[sys.executable,'-m','PyInstaller','--noconfirm','--clean',
    '--onefile' if old else '--onedir','--console','--name','qtifw-trial-helper',
    '--paths',ROOT/'app','--paths',sources,'--exclude-module','PySide6','--exclude-module','numpy','--exclude-module','cv2',
    *(['--exclude-module','PIL'] if args.portable else []),
    '--distpath',runroot/'helper','--workpath',runroot/'freeze-work','--specpath',runroot,
    '--add-data',str(runroot/'qtifw-trial-policy.json')+';.',sources/'qtifw_trial_helper.py'],180)
assert code==0, 'freeze failed'
helper_root=runroot/'helper' if old else runroot/'helper/qtifw-trial-helper'
files=['qtifw-trial-helper.exe'] if old else sorted(p.relative_to(helper_root).as_posix() for p in helper_root.rglob('*') if p.is_file())
qrc=ET.Element('RCC');resources=ET.SubElement(qrc,'qresource',prefix='/qtifw-trial')
for name in files:
    ET.SubElement(resources,'file',alias='helper.exe' if old else name).text=str(helper_root/name)
ET.ElementTree(qrc).write(runroot/'trial.qrc',encoding='utf-8',xml_declaration=True)

def build(name,broken=False,copy_fault=False):
    case=runroot/name;case.mkdir();config=case/'config';config.mkdir()
    meta=case/'packages'/PACKAGE/'meta';meta.mkdir(parents=True)
    (meta/'install-location.ui').write_bytes(source('tools/qtifw_trial/install-location.ui'))
    write_xml(meta/'package.xml','Package',dict(DisplayName='Synthetic program',Version='1.0.0',ReleaseDate='2026-11-08',Default='true',Script='component.js',Dependencies=DATA))
    tree=ET.parse(meta/'package.xml');ET.SubElement(ET.SubElement(tree.getroot(),'UserInterfaces'),'UserInterface').text='install-location.ui';tree.write(meta/'package.xml',encoding='utf-8')
    data=case/'packages'/PACKAGE/'data/program'
    for n,b in values.items():
        p=data/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'CORRUPTED\n' if broken and n.endswith('runtime.txt') else b)
    copies=[]
    if args.portable:
        from build_qtifw_trial import deduplicate_payload
        copies,stats=deduplicate_payload(case,{'files':policy['files']},helper_root)
        assert stats['copiedProgramFiles']==3,stats
        if copy_fault:
            copies.append(['@TargetDir@/program/missing.bin','@TargetDir@/program/failed-copy.bin'])
    (meta/'component.js').write_bytes(source('tools/qtifw_trial/component.js').replace(b'__PROGRAM_COPIES__',json.dumps(copies).encode('utf-8')))
    dm=case/'packages'/DATA/'meta';dm.mkdir(parents=True)
    (dm/'data-component.js').write_bytes(source('tools/qtifw_trial/data-component.js').replace(b'__SUPPORT_FILES__',json.dumps(files).encode('utf-8')))
    write_xml(dm/'package.xml','Package',dict(DisplayName='Synthetic cleanup',Version='1.0.0',ReleaseDate='2026-11-08',Virtual='true',Script='data-component.js'))
    text=source('tools/qtifw_trial/controller.js').decode('utf-8').replace('__SCOPE__',json.dumps('' if args.portable else runroot.as_posix())).replace('__INSTANCE__',json.dumps(instance)).replace('__SUPPORT_FILES__',json.dumps(files)).replace('__UPGRADE_FILES__','[]')
    # Only OS shortcut locations are redirected. Core helper and native
    # operations are the actual PR scripts, with no simulated algorithms.
    text=text.replace('function Controller() {','function Controller() {\ninstaller.setValue("DesktopDir",'+json.dumps(str(case/'desktop'))+');\ninstaller.setValue("UserStartMenuProgramsPath",'+json.dumps(str(case/'programs'))+');',1)
    (config/'controller.js').write_text(text,encoding='utf-8')
    write_xml(config/'config.xml','Installer',dict(Name='PR110 audit '+uuid.uuid4().hex,Version='1.0.0',Title='PR110 owned synthetic review',
        TargetDir=str(runroot/'targets'/name),MaintenanceToolName='trial-maintenance',RemoveTargetDir='true',
        AllowNonAsciiCharacters='true',AllowSpaceInPath='true',ControlScript='controller.js'))
    setup=case/'setup.exe';assert call(name+'-build',[IFW/'binarycreator.exe','--offline-only','--resources',runroot/'trial.qrc','-c',config/'config.xml','-p',case/'packages',setup])==0
    return case,setup,runroot/'targets'/name

report=dict(revision=args.baseline_ref if old else subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            uncommittedChanges=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip()),
            baselineCounterexamples=old,portable=args.portable,syntheticFiles=len(values),actualProgramLaunched=False)

def install(name,broken=False,post_fault=False,copy_fault=False):
    case,setup,target=build(name,broken,copy_fault)
    command=[setup,'--reject-messages','--confirm-command']
    if post_fault:
        script=case/'post-fault.js';script.write_text('''eval(installer.readFile(":/metadata/installer-config/controller_js", "UTF-8"));
var Original=Controller;Controller=function(){
installer.installationFinished.connect(function(){if(installer.status===QInstaller.Success)installer.performOperation("Delete",[installer.value("TargetDir")+"/program/_internal/runtime.txt"]);});
Original.call(this);};Controller.prototype=Original.prototype;
''',encoding='utf-8');command+=['--script',script]
    code=call(name+'-install',command+['install','TrialTarget='+str(target)])
    report[name]=dict(exitCode=code,mainPresent=(target/'program/星图.exe').exists(),maintenancePresent=(target/'trial-maintenance.exe').exists())
    if code==0 and not broken and not post_fault:
        for n,b in values.items():
            assert (target/'program'/n).read_bytes()==b,n
            assert (target/'program'/n).stat().st_nlink==1,n
        if args.portable:
            for path in (target/'_installer-support').rglob('*'):
                if path.is_file():
                    assert not path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY,path
    print(which,name,report[name],flush=True)
    return case,setup,target

if args.portable:
    outside=runroot/'copy-fault-outside.txt';outside.write_bytes(b'outside preserved')
    _,_,target=install('copy-fault',copy_fault=True)
    assert report['copy-fault']['exitCode']!=0
    assert not (target/'program').exists() and not (target/'_installer-support').exists()
    assert outside.read_bytes()==b'outside preserved'
    report['nativeCopyFailureRolledBack']=True

case,_,target=install('corrupt-archive',broken=True)
assert report['corrupt-archive']['exitCode']==0 if old else report['corrupt-archive']['exitCode']!=0
if (target/'trial-maintenance.exe').exists():call('corrupt-cleanup',[target/'trial-maintenance.exe','--reject-messages','--confirm-command','purge'])
case,_,target=install('post-fault',post_fault=True)
assert report['post-fault']['exitCode']==0 if old else report['post-fault']['exitCode']!=0
if (target/'trial-maintenance.exe').exists():call('post-cleanup',[target/'trial-maintenance.exe','--reject-messages','--confirm-command','purge'])

lease_code='''import sys;sys.path.insert(0,sys.argv[1]);from resource_transaction import resource_runtime_lease
try:lease=resource_runtime_lease(sys.argv[2])
except (OSError,ValueError):print("blocked",flush=True);raise SystemExit(2)
print("held",flush=True);sys.stdin.readline();lease.close()
'''
def lease(target):
    p=subprocess.Popen([sys.executable,'-u','-c',lease_code,str(ROOT/'app'),str(target/'program')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    return p,p.stdout.readline().strip()

case,_,target=install('late-lease');assert report['late-lease']['exitCode']==0
if not old:
    pointer=target/'program/work/author-snapshots/activation.json'
    pointer.parent.mkdir(parents=True,exist_ok=True);pointer.write_bytes(b'owned snapshot publication')
    os.link(pointer,pointer.with_name('publication-witness.json'))
    report['internalHardlinkPair']=True
if args.portable:
    # Preserve, but make the install-time support paths unavailable. Uninstall
    # must recreate its own admission helper and use the installed UNDO helper.
    previous=list((runroot/'temp').glob('xingtu-setup-*'))
    assert previous, 'Portable installer did not use its runtime temporary directory'
    for path in previous:path.rename(path.with_name('preserved-'+path.name))
    report['originalInstallerSupportUnavailable']=True
p,state=lease(target);assert state=='held'
active=call('active-before-purge',[target/'trial-maintenance.exe','--reject-messages','--confirm-command','purge']);assert active!=0 and (target/'program/星图.exe').exists()
p.communicate('\n',timeout=10);report['activeBeforePurgeRejected']=True
auto=case/'auto.js';auto.write_text('''eval(installer.readFile(":/metadata/installer-config/controller_js","UTF-8"));
installer.setMessageBoxAutomaticAnswer("installationErrorWithIgnore",QMessageBox.Ignore);
var ready=Controller.prototype.ReadyForInstallationPageCallback;
Controller.prototype.ReadyForInstallationPageCallback=function(){ready.call(this);console.log("AUDIT_READY");gui.clickButton(buttons.CommitButton,4000);};
installer.uninstallationFinished.connect(function(){gui.clickButton(buttons.NextButton,200);});
var done=Controller.prototype.FinishedPageCallback;Controller.prototype.FinishedPageCallback=function(){done.call(this);gui.clickButton(buttons.FinishButton,200);};
''',encoding='utf-8')
logpath=runroot/'late-gui.log'
with logpath.open('wb') as log:
    native=subprocess.Popen([str(target/'trial-maintenance.exe'),'--verbose','--script',str(auto)],stdout=log,stderr=subprocess.STDOUT)
    for _ in range(150):
        if b'AUDIT_READY' in logpath.read_bytes():break
        if native.poll() is not None:raise AssertionError('native uninstaller exited before ready')
        time.sleep(.1)
    else:raise AssertionError('no native ready page')
    late,state=lease(target);report['lateLease']=state
    assert state==('held' if old else 'blocked'), state
    native.wait(timeout=45)
    report['lateUninstallExitCode']=native.returncode
    assert native.returncode==0, 'Late-lease uninstall did not complete'
    report['lateProgramRemoved']=not (target/'program/星图.exe').exists()
    late.communicate('\n',timeout=10)
    for _ in range(100):
        if not target.exists():break
        time.sleep(.1)
    report['lateTargetRemains']=target.exists()
    if not old:assert not target.exists(), 'Final native cleanup did not complete'
print(which,'late lease',report['lateLease'],'root remains',target.exists(),flush=True)

case,_,target=install('pending');assert report['pending']['exitCode']==0
journal=target/'program/work/map-packages/pending-base-repair.json';journal.parent.mkdir(parents=True,exist_ok=True);journal.write_text('{"syntheticInterrupted":true}')
outside=target.parent/'outside-pending.txt';outside.write_bytes(b'keep external')
code=call('pending-purge',[target/'trial-maintenance.exe','--reject-messages','--confirm-command','purge'])
for _ in range(100):
    if not target.exists():break
    time.sleep(.1)
report['pendingPurge']=dict(exitCode=code,targetRemains=target.exists(),outsidePreserved=outside.read_bytes()==b'keep external')
assert report['pendingPurge']['outsidePreserved'], 'External sentinel was modified'
assert code!=0 if old else code==0 and not target.exists()
if old:
    journal.rename(runroot/'preserved-pending-fault.json')
    call('pending-fixture-cleanup',[target/'trial-maintenance.exe','--reject-messages','--confirm-command','purge'])
else:
    case,_,target=install('cancel');assert report['cancel']['exitCode']==0
    cancel=case/'cancel.js';cancel.write_text('''eval(installer.readFile(":/metadata/installer-config/controller_js","UTF-8"));
installer.autoAcceptMessageBoxes();
var ready=Controller.prototype.ReadyForInstallationPageCallback;
Controller.prototype.ReadyForInstallationPageCallback=function(){ready.call(this);console.log("AUDIT_READY");gui.clickButton(buttons.CancelButton,3000);};
''',encoding='utf-8')
    cancel_log=runroot/'cancel-gui.log'
    with cancel_log.open('wb') as log:
        native=subprocess.Popen([str(target/'trial-maintenance.exe'),'--verbose','--script',str(cancel)],stdout=log,stderr=subprocess.STDOUT)
        for _ in range(150):
            if b'AUDIT_READY' in cancel_log.read_bytes():break
            if native.poll() is not None:raise AssertionError('Cancel flow exited before ready')
            time.sleep(.1)
        busy,state=lease(target);assert state=='blocked';busy.communicate('\n',timeout=10)
        native.wait(timeout=30)
    free,state=lease(target);assert state=='held';free.communicate('\n',timeout=10)
    assert (target/'program/星图.exe').read_bytes()==values['星图.exe']
    report['cancelKeepsProgramAndReleasesLease']=True
    assert call('cancel-fixture-cleanup',[target/'trial-maintenance.exe','--reject-messages','--confirm-command','purge'])==0
report['completed']=True
(runroot/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True),flush=True)
