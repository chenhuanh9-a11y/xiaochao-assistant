{ Shared full uninstall. Upgrades never call this entry.
  usUninstall is the last abortable boundary before native Inno removal.
  https://jrsoftware.org/ishelp/topic_scriptevents.htm }
#ifndef UninstallHelperInventorySHA256
  #error UninstallHelperInventorySHA256 is required; perform a full helper freeze
#endif
#ifndef UninstallOwnershipSHA256
  #error UninstallOwnershipSHA256 is required; build the final program ownership list
#endif
type
  { Inno 6.7.3 uses a 32-bit loader, including x64-compatible installations. }
  TUninstallStartup = record
    Size, Reserved, Desktop, Title, X, Y, XSize, YSize, XChars, YChars,
      Fill, Flags: LongWord;
    ShowWindow, ReservedSize: Word;
    ReservedData: LongWord;
    Input, Output, Error: THandle;
  end;
  TUninstallProcess = record
    Process, Thread: THandle;
    Pid, Tid: LongWord;
  end;
  TUninstallMessage = record
    Window: THandle;
    Message, WParam, LParam, Time: LongWord;
    X, Y: Integer;
    PrivateData: LongWord;
  end;
var
  MaintenanceMutex: THandle;
  UninstallLease: THandle;
  UninstallRunnerRoot, UninstallResultRoot: String;
  UninstallMayHaveChanged, UninstallCleanupComplete: Boolean;
  UninstallProcessUncertain: Boolean;
  UninstallAttempt: Integer;
  RecoveryForm: TSetupForm;
  RecoveryStatus: TNewStaticText;
  RecoveryLeaseRoot: String;

function ContinueUninstallRecovery(FromSetup: Boolean): Boolean; forward;

function HasUninstallRecovery: Boolean;
var Key: String;
begin
  Key := 'Software\Xingtu\UninstallRecovery\' + ExpandConstant('{#InstallerAppId}');
#ifdef ProductionInstaller
  Result := RegKeyExists(HKCU64, Key);
#else
  Result := RegKeyExists(HKLM64, Key);
#endif
end;

function UninstallRecoveryIsPrepared: Boolean;
var Key, State: String; Root: Integer;
begin
  { This only restricts entry; it never grants deletion. The helper validates
    the complete registration again under its verified live lease. Unknown or
    unreadable state conservatively goes to the matching Setup. }
  Key := 'Software\Xingtu\UninstallRecovery\' + ExpandConstant('{#InstallerAppId}');
#ifdef ProductionInstaller
  Root := HKCU64;
#else
  Root := HKLM64;
#endif
  Result := RegQueryStringValue(Root, Key, 'State', State) and
    (Pos('"phase":"prepared"', State) > 0);
end;

function UninstallCreateFile(Path: String; Access, Sharing, Security,
  Disposition, Flags, Template: LongWord): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function UninstallCloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function UninstallCurrentPid: LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function UninstallCurrentProcess: THandle;
  external 'GetCurrentProcess@kernel32.dll stdcall';
function UninstallProcessTimes(Handle: THandle; var Created, Exited, Kernel, User: Int64): Boolean;
  external 'GetProcessTimes@kernel32.dll stdcall';
function UninstallModuleName(Module: THandle; Buffer: String; Size: LongWord): LongWord;
  external 'GetModuleFileNameW@kernel32.dll stdcall';
function UninstallLastError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';
function UninstallDeleteByHandle(Handle: THandle; InfoClass: LongWord;
  var DeleteFile: Byte; Size: LongWord): Boolean;
  external 'SetFileInformationByHandle@kernel32.dll stdcall';
function UninstallCreateProcess(ApplicationName, CommandLine: String;
  ProcessSecurity, ThreadSecurity: LongWord; InheritHandles: Boolean;
  Flags, Environment: LongWord; Directory: String;
  var Startup: TUninstallStartup; var Process: TUninstallProcess): Boolean;
  external 'CreateProcessW@kernel32.dll stdcall';
function UninstallWait(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function UninstallTerminate(Handle: THandle; Code: LongWord): Boolean;
  external 'TerminateProcess@kernel32.dll stdcall';
function UninstallExitCode(Handle: THandle; var Code: LongWord): Boolean;
  external 'GetExitCodeProcess@kernel32.dll stdcall';
function UninstallTicks: Int64;
  external 'GetTickCount64@kernel32.dll stdcall';
function UninstallPeekMessage(var Message: TUninstallMessage;
  Window: THandle; First, Last, Remove: LongWord): Boolean;
  external 'PeekMessageW@user32.dll stdcall';
function UninstallTranslateMessage(var Message: TUninstallMessage): Boolean;
  external 'TranslateMessage@user32.dll stdcall';
function UninstallDispatchMessage(var Message: TUninstallMessage): LongWord;
  external 'DispatchMessageW@user32.dll stdcall';
function InstallerMaintenanceMutex(Security: LongWord; InitialOwner: Boolean;
  Name: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';

function AcquireInstallerMaintenance: Boolean;
var Error: LongWord;
begin
  Result := True;
  if MaintenanceMutex <> 0 then Exit;
  { Closing the data lock before native removal otherwise permits another
    Setup to restore files while uninstall is deleting them. Both entry points
    atomically create this same mutex and hold it until process exit. }
  MaintenanceMutex := InstallerMaintenanceMutex(0, False,
    ExpandConstant('Global\XingtuMaintenance-{#InstallerAppId}'));
  Error := UninstallLastError;
  if (MaintenanceMutex = 0) or (Error = 183) then begin
    if MaintenanceMutex <> 0 then UninstallCloseHandle(MaintenanceMutex);
    MaintenanceMutex := 0;
    SuppressibleMsgBox('本渠道的安装或卸载仍在进行，或当前账号无法取得维护锁。请等待其结束后再重试。', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := AcquireInstallerMaintenance;
  if Result and HasUninstallRecovery then begin
    { Recovery owns this invocation. Never fall through into upgrade/preserve,
      even on cancel, corrupt credentials, wrong build, or failed cleanup. }
    ContinueUninstallRecovery(True);
    Result := False;
  end;
end;

procedure UninstallPumpMessages;
var Message: TUninstallMessage; I: Integer;
begin
  { Bound the pump too: an input flood must not defeat the worker deadline. }
  for I := 1 to 32 do begin
    if not UninstallPeekMessage(Message, 0, 0, 0, 1) then Break;
    UninstallTranslateMessage(Message);
    UninstallDispatchMessage(Message);
  end;
end;

function RunBoundedUninstallProcess(const Executable, Params, Directory: String;
  Timeout: LongWord; var Code: Integer): Boolean;
var Startup: TUninstallStartup; Process: TUninstallProcess;
  Started: Int64; State, NativeCode: LongWord; Caption: String;
  Form: TUninstallProgressForm;
begin
  Result := False;
  Code := -1;
  Form := nil;
  if UninstallProcessUncertain then Exit;
  Startup.Size := SizeOf(Startup);
  Startup.Flags := 1;
  Startup.ShowWindow := SW_HIDE;
  { CreateProcess returns the exact child handle. No PID/name lookup or kill. }
  if not UninstallCreateProcess(Executable, AddQuotes(Executable) + ' ' + Params,
    0, 0, False, $08000000, 0, Directory, Startup, Process) then Exit;
  UninstallProcessUncertain := True;
  try
    UninstallCloseHandle(Process.Thread);
    Started := UninstallTicks;
    { DeinitializeUninstall runs after Inno frees its progress form. }
    { Setup also uses this bounded worker. Its uninstall form does not exist. }
    if IsUninstaller then Form := UninstallProgressForm;
    if Form <> nil then begin
      Caption := Form.StatusLabel.Caption;
      Form.CancelButton.Enabled := False;
    end;
    repeat
      State := UninstallWait(Process.Process, 100);
      if State = 0 then begin
        UninstallProcessUncertain := False;
        if UninstallExitCode(Process.Process, NativeCode) then begin
          Code := Integer(NativeCode);
          Result := True;
        end;
        Exit;
      end;
      if (State <> 258) or (UninstallTicks - Started >= Timeout) then Break;
      if Form <> nil then
        Form.StatusLabel.Caption := Caption + '（已等待 ' +
          IntToStr((UninstallTicks - Started) div 1000) + ' 秒）';
      if RecoveryStatus <> nil then
        RecoveryStatus.Caption := '正在核对本次卸载的剩余项并清理…（已等待 ' +
          IntToStr((UninstallTicks - Started) div 1000) + ' 秒）';
      UninstallPumpMessages;
    until False;
    { Stop only this invocation. Do not trust an old result or expose Retry
      until Windows confirms that this exact process can no longer mutate. }
    Log('UNINSTALL_WORKER_LIMIT_REACHED');
    UninstallTerminate(Process.Process, 1460);
    Started := UninstallTicks;
    repeat
      State := UninstallWait(Process.Process, 100);
      if State = 0 then begin
        UninstallProcessUncertain := False;
        Break;
      end;
      UninstallPumpMessages;
    until (State <> 258) or (UninstallTicks - Started >= 5000);
  finally
    if UninstallProcessUncertain then begin
      UninstallTerminate(Process.Process, 1460);
      if UninstallWait(Process.Process, 5000) = 0 then
        UninstallProcessUncertain := False;
    end;
    UninstallCloseHandle(Process.Process);
    if Form <> nil then begin
      Form.StatusLabel.Caption := Caption;
      Form.CancelButton.Enabled := not UninstallProcessUncertain;
    end;
  end;
end;

function UninstallHoldingDirectory: String;
begin
  Result := ExpandConstant('{app}\.xingtu-uninstall-retained');
end;

function UninstallSafeAbsolute(Path: String): Boolean;
var Parent: String; Attributes, Error: LongWord;
begin
  Result := False;
  repeat
    Attributes := InstallerFileAttributes(Path);
    if Attributes = $FFFFFFFF then begin
      Error := UninstallLastError;
      if (Error <> 2) and (Error <> 3) then Exit;
    end else if (Attributes and $400) <> 0 then Exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Break;
    Path := Parent;
  until Path = '';
  Result := True;
end;

function UninstallReadText(const Path: String): String;
var Lines: TArrayOfString; I: Integer;
begin
  Result := '';
  if LoadStringsFromFile(Path, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do Result := Result + Lines[I] + #13#10;
end;

procedure CheckUninstallShortcut(const Path: String);
var Shell, Link: Variant; Target, App: String;
begin
  if not UninstallSafeAbsolute(Path) then RaiseException('快捷方式含链接或无法读取，尚未删除资料：' + Path);
  if DirExists(Path) then RaiseException('快捷方式位置是目录，尚未删除资料：' + Path);
  if not FileExists(Path) then Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Link := Shell.CreateShortcut(Path);
    Target := RemoveBackslashUnlessRoot(ExpandFileName(String(Link.TargetPath)));
    App := AddBackslash(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))));
    if (CompareText(Target, App + '{#InstallerExeName}') <> 0) and
      (CompareText(Target, App + '第五人格小抄助手.exe') <> 0) and
      (CompareText(Target, ExpandConstant('{uninstallexe}')) <> 0) then
      RaiseException('快捷方式现在指向其他程序，未删除资料或入口：' + Path);
  except
    RaiseException('无法确认快捷方式属于本次安装，尚未删除资料：' + Path + #13#10 + GetExceptionMessage);
  end;
end;

procedure CheckUninstallShortcuts;
begin
  CheckUninstallShortcut(ExpandConstant('{group}\{#InstallerAppName}.lnk'));
  CheckUninstallShortcut(ExpandConstant('{autodesktop}\{#InstallerAppName}.lnk'));
#ifdef ProductionInstaller
  CheckUninstallShortcut(ExpandConstant('{group}\卸载星图.lnk'));
  CheckUninstallShortcut(ExpandConstant('{group}\卸载星图（保留个人资料）.lnk'));
  CheckUninstallShortcut(ExpandConstant('{userdesktop}\第五人格小抄助手.lnk'));
  CheckUninstallShortcut(ExpandConstant('{userprograms}\第五人格小抄助手\第五人格小抄助手.lnk'));
#endif
end;

procedure RemoveOwnedUninstallShortcut(const Path: String);
begin
  CheckUninstallShortcut(Path);
  if FileExists(Path) and not DeleteFile(Path) then
    RaiseException('快捷方式未能移除：' + Path);
end;

procedure FinishUninstallEntries;
begin
#ifdef ProductionInstaller
  { Keep current native shortcuts/registration until Inno removes them. }
  RemoveOwnedUninstallShortcut(ExpandConstant('{group}\卸载星图（保留个人资料）.lnk'));
  RemoveOwnedUninstallShortcut(ExpandConstant('{userdesktop}\第五人格小抄助手.lnk'));
  RemoveOwnedUninstallShortcut(ExpandConstant('{userprograms}\第五人格小抄助手\第五人格小抄助手.lnk'));
#endif
end;

procedure ReleaseUninstallLease;
begin
  if (UninstallLease <> 0) and (UninstallLease <> THandle(-1)) then
    UninstallCloseHandle(UninstallLease);
  UninstallLease := 0;
end;

procedure FinishUninstallData;
var Path: String; Disposition: Byte;
begin
  { The exact helper has exited, and ordinary product writers were removed.
    Finish the data critical section before native removes the retry runtime.
    The process-lifetime maintenance mutex continues to exclude Setup. }
  Disposition := 1;
  if (UninstallLease = 0) or not UninstallDeleteByHandle(UninstallLease, 4, Disposition, 1) then
    RaiseException('资料尚未全部清除，资源锁文件无法移除。请关闭本安装的资源任务后重试。');
  ReleaseUninstallLease;
  Path := ExpandConstant('{app}\work\map-packages\import.lock');
  if not UninstallSafeAbsolute(Path) or FileExists(Path) then
    RaiseException('资料尚未全部清除，资源锁仍被占用。请关闭相关任务后重试。');
  if DirExists(ExtractFileDir(Path)) and not RemoveDir(ExtractFileDir(Path)) then
    RaiseException('资料尚未全部清除，资源目录仍有剩余项或无法移除。请核对后重试。');
  Path := ExpandConstant('{app}\work');
  if not UninstallSafeAbsolute(Path) or (DirExists(Path) and not RemoveDir(Path)) then
    RaiseException('资料尚未全部清除，资料目录仍有剩余项或无法移除。请核对后重试。');
end;

procedure AcquireUninstallLease;
var Path: String; Disposition: LongWord;
begin
  Path := ExpandConstant('{app}\work\map-packages\import.lock');
  if not UninstallSafeAbsolute(Path) then
    RaiseException('安装路径含链接或无法访问，尚未开始卸载。');
  Disposition := 4;
  if HasUninstallRecovery then begin
    { Never recreate a deleted lock tail around an existing transaction. }
    if not UninstallRecoveryIsPrepared or not FileExists(Path) then
      RaiseException('已有未完成的卸载，请关闭当前窗口，再打开卸载时的同一份安装包继续清理。');
    Disposition := 3;
  end else if not ForceDirectories(ExtractFileDir(Path)) then
    RaiseException('无法取得本安装的资料目录，尚未开始卸载。');
  UninstallLease := UninstallCreateFile(Path, $C0010000, 0, 0, Disposition, $80, 0);
  if UninstallLease = THandle(-1) then begin
    UninstallLease := 0;
    RaiseException('星图或资源任务正在使用资料，请关闭任务后重试。');
  end;
end;

procedure PrepareUninstallRunner;
var Inventory, Relative, Source, Target, Digest: String; Lines: TArrayOfString; I: Integer;
begin
  Inventory := ExpandConstant('{app}\_uninstall-helper-files.txt');
  if not UninstallSafeAbsolute(Inventory) or not FileExists(Inventory) or
    (GetSHA256OfFile(Inventory) <> '{#UninstallHelperInventorySHA256}') or
    not LoadStringsFromFile(Inventory, Lines) or (GetArrayLength(Lines) = 0) then
    RaiseException('卸载检查程序的文件清单无法验证，请使用对应安装包修复后重试。');
  UninstallAttempt := UninstallAttempt + 1;
  UninstallRunnerRoot := ExpandConstant('{tmp}\xingtu-uninstall-runner-') + IntToStr(UninstallAttempt);
  UninstallResultRoot := ExpandConstant('{tmp}\xingtu-uninstall-result-') + IntToStr(UninstallAttempt);
  if not UninstallSafeAbsolute(UninstallRunnerRoot) or
    not UninstallSafeAbsolute(UninstallResultRoot) or
    not CreateDir(UninstallRunnerRoot) or not CreateDir(UninstallResultRoot) then
    RaiseException('无法创建独立的卸载检查目录，尚未删除资料。');
  if not FileCopy(Inventory, UninstallRunnerRoot + '\_uninstall-helper-files.txt', True) or
    (GetSHA256OfFile(UninstallRunnerRoot + '\_uninstall-helper-files.txt') <> '{#UninstallHelperInventorySHA256}') then
    RaiseException('无法保留已核实的继续清理库存，尚未删除资料。');
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    if (Length(Lines[I]) < 66) or (Copy(Lines[I], 65, 1) <> ' ') then
      RaiseException('卸载检查程序的文件清单无效。');
    Digest := Copy(Lines[I], 1, 64);
    Relative := Copy(Lines[I], 66, Length(Lines[I]));
    if (Pos('..', Relative) > 0) or (Pos(':', Relative) > 0) or
      (Pos('/', Relative) > 0) or (Relative[1] = '\') or
      ((CompareText(Relative, 'star-map-uninstall-helper.exe') <> 0) and
       (Pos('_star_map_uninstall_runtime\', Relative) <> 1)) then
      RaiseException('卸载检查程序含无效的相对路径。');
    Source := ExpandConstant('{app}\') + Relative;
    Target := AddBackslash(UninstallRunnerRoot) + Relative;
    if not UninstallSafeAbsolute(Source) or not UninstallSafeAbsolute(Target) or
      not FileExists(Source) or (GetSHA256OfFile(Source) <> Digest) or
      not ForceDirectories(ExtractFileDir(Target)) or not FileCopy(Source, Target, True) or
      (GetSHA256OfFile(Target) <> Digest) or (GetSHA256OfFile(Source) <> Digest) then
      RaiseException('卸载检查程序已变化或无法完整复制，尚未删除资料。');
  end;
end;

procedure ClearOwnedInstallation;
var Params, ParentImage, Detail, Receipt: String; Code: Integer; Size: LongWord;
  Created, Exited, KernelTime, UserTime: Int64;
  PreviouslyChanged: Boolean;
begin
  SetLength(ParentImage, 32768);
  Size := UninstallModuleName(0, ParentImage, 32768);
  if (Size = 0) or (Size >= 32768) or not UninstallProcessTimes(UninstallCurrentProcess,
    Created, Exited, KernelTime, UserTime) then
    RaiseException('无法核实当前卸载器进程，尚未删除资料。');
  SetLength(ParentImage, Size);
  Params := '--install-root ' + AddQuotes(ExpandConstant('{app}')) +
    ' --result-directory ' + AddQuotes(UninstallResultRoot) +
    ' --parent-pid ' + IntToStr(UninstallCurrentPid) + ' --parent-created ' + IntToStr(Created) +
    ' --parent-executable ' + AddQuotes(ParentImage) + ' --parent-sha256 ' + GetSHA256OfFile(ParentImage) +
    ' --lock-handle ' + IntToStr(UninstallLease) + ' --timeout-seconds 120' +
    ' --helper-inventory-sha256 {#UninstallHelperInventorySHA256}' +
    ' --program-ownership-sha256 {#UninstallOwnershipSHA256}' +
    ' --shortcut ' + AddQuotes(ExpandConstant('{group}\{#InstallerAppName}.lnk')) +
    ' --shortcut ' + AddQuotes(ExpandConstant('{autodesktop}\{#InstallerAppName}.lnk')) +
    ' --shortcut-group ' + AddQuotes(ExpandConstant('{group}'));
#ifdef ProductionInstaller
  Params := Params + ' --shortcut ' + AddQuotes(ExpandConstant('{group}\卸载星图.lnk')) +
    ' --shortcut ' + AddQuotes(ExpandConstant('{group}\卸载星图（保留个人资料）.lnk')) +
    ' --shortcut ' + AddQuotes(ExpandConstant('{userdesktop}\第五人格小抄助手.lnk')) +
    ' --shortcut ' + AddQuotes(ExpandConstant('{userprograms}\第五人格小抄助手\第五人格小抄助手.lnk')) +
    ' --shortcut-group ' + AddQuotes(ExpandConstant('{userprograms}\第五人格小抄助手'));
#endif
  Code := -1;
  PreviouslyChanged := UninstallMayHaveChanged;
  UninstallMayHaveChanged := True;
  if not RunBoundedUninstallProcess(UninstallRunnerRoot + '\star-map-uninstall-helper.exe', Params,
    UninstallRunnerRoot, 135000, Code) then begin
    if UninstallProcessUncertain then
      RaiseException('清理未完成，且尚未确认检查程序停止。现在不要重试或启动星图；请重启电脑后重新卸载。');
    RaiseException('卸载检查程序无法启动、已崩溃或超过等待时间，现已确认停止。资料可能只清除了一部分；请关闭相关任务后重试。');
  end;
  Receipt := UninstallResultRoot + '\result.ini';
  if (GetIniString('result', 'status', '', Receipt) = 'error') and
    (GetIniString('result', 'mutationStarted', '', Receipt) = 'False') then
    UninstallMayHaveChanged := PreviouslyChanged;
  if (Code <> 0) or (GetIniString('result', 'status', '', Receipt) <> 'cleared') then begin
    Detail := UninstallReadText(UninstallResultRoot + '\error.txt') +
      UninstallReadText(UninstallResultRoot + '\remaining.txt');
    Log('UNINSTALL_FULL_CLEAR_FAILED ' + Detail);
    if not HasUninstallRecovery and UninstallSafeAbsolute(ExpandConstant('{app}\.xingtu-uninstall-failed.txt')) then
      SaveStringToFile(ExpandConstant('{app}\.xingtu-uninstall-failed.txt'), Detail, False);
    RaiseException('资料尚未全部清除，程序卸载尚未开始。已删除的部分不能自动撤回。' + #13#10 +
      GetIniString('result', 'message', '请关闭相关任务并核对剩余项，再重试；安装文件缺失时用对应安装包修复。', Receipt));
  end;
  Log('UNINSTALL_OWNED_DATA_CLEARED_BEFORE_NATIVE');
end;

function ContinueUninstallRecovery(FromSetup: Boolean): Boolean;
var Params, ParentImage, Inventory, Relative, Source, Digest, Detail: String;
  Lines: TArrayOfString; I, Code: Integer; Size: LongWord;
  Created, Exited, KernelTime, UserTime: Int64;
begin
  Result := False;
  if UninstallProcessUncertain then Exit;
  if FromSetup then begin
    if WizardSilent then Exit;
    if SuppressibleMsgBox('上次卸载尚未完成。本安装包可以核对并继续清理那次卸载留下的程序和入口。' + #13#10 +
      '只处理卸载前已核实的对象；归属不明或被替换的项目会保留并停止。本次不会安装新版本。继续清理？',
      mbConfirmation, MB_YESNO, IDNO) <> IDYES then Exit;
  end;
  try
    if FromSetup then begin
      ExtractTemporaryFiles('{tmp}\xingtu-native-recovery\*');
      UninstallRunnerRoot := ExpandConstant('{tmp}\xingtu-native-recovery');
    end;
    Inventory := UninstallRunnerRoot + '\_uninstall-helper-files.txt';
    if not FileExists(Inventory) then begin
      { The original uninstaller's verified runtime did not need the text
        inventory at execution time; retain its verified copy for continuation. }
      Inventory := ExpandConstant('{app}\_uninstall-helper-files.txt');
    end;
    if not UninstallSafeAbsolute(Inventory) or
      (GetSHA256OfFile(Inventory) <> '{#UninstallHelperInventorySHA256}') or
      not LoadStringsFromFile(Inventory, Lines) or (GetArrayLength(Lines) = 0) then
      RaiseException('对应安装包的清理程序无法验证，请保留剩余项并使用卸载时的同一份安装包。');
    for I := 0 to GetArrayLength(Lines) - 1 do begin
      if (Length(Lines[I]) < 66) or (Copy(Lines[I], 65, 1) <> ' ') then
        RaiseException('清理程序的文件清单无效。');
      Digest := Copy(Lines[I], 1, 64);
      Relative := Copy(Lines[I], 66, Length(Lines[I]));
      if (Pos('..', Relative) > 0) or (Pos(':', Relative) > 0) or
        (Pos('/', Relative) > 0) or (Relative[1] = '\') or
        ((CompareText(Relative, 'star-map-uninstall-helper.exe') <> 0) and
         (Pos('_star_map_uninstall_runtime\', Relative) <> 1)) then
        RaiseException('清理程序含无效相对路径。');
      Source := AddBackslash(UninstallRunnerRoot) + Relative;
      if not UninstallSafeAbsolute(Source) or not FileExists(Source) or
        (GetSHA256OfFile(Source) <> Digest) then RaiseException('对应安装包的清理程序已变化，已停止。');
    end;
    UninstallAttempt := UninstallAttempt + 1;
    UninstallResultRoot := ExpandConstant('{tmp}\xingtu-recovery-result-') + IntToStr(UninstallAttempt);
    RecoveryLeaseRoot := ExpandConstant('{tmp}\xingtu-recovery-lease-') + IntToStr(UninstallAttempt);
    if not UninstallSafeAbsolute(UninstallResultRoot) or not CreateDir(UninstallResultRoot) or
      not UninstallSafeAbsolute(RecoveryLeaseRoot) or not CreateDir(RecoveryLeaseRoot) or
      not ForceDirectories(RecoveryLeaseRoot + '\work\map-packages') then
      RaiseException('无法创建独立的继续清理目录，已停止。');
    UninstallLease := UninstallCreateFile(RecoveryLeaseRoot + '\work\map-packages\import.lock',
      $C0010000, 0, 0, 4, $80, 0);
    if UninstallLease = THandle(-1) then begin
      UninstallLease := 0;
      RaiseException('继续清理的资源锁无法取得，已停止。');
    end;
    SetLength(ParentImage, 32768);
    Size := UninstallModuleName(0, ParentImage, 32768);
    if (Size = 0) or (Size >= 32768) or not UninstallProcessTimes(UninstallCurrentProcess,
      Created, Exited, KernelTime, UserTime) then RaiseException('当前清理进程无法核实，已停止。');
    SetLength(ParentImage, Size);
    Params := '--recover --install-root ' + AddQuotes('@registered') +
      ' --result-directory ' + AddQuotes(UninstallResultRoot) +
      ' --recovery-lease-root ' + AddQuotes(RecoveryLeaseRoot) +
      ' --helper-inventory-sha256 {#UninstallHelperInventorySHA256}' +
      ' --parent-pid ' + IntToStr(UninstallCurrentPid) + ' --parent-created ' + IntToStr(Created) +
      ' --parent-executable ' + AddQuotes(ParentImage) + ' --parent-sha256 ' + GetSHA256OfFile(ParentImage) +
      ' --lock-handle ' + IntToStr(UninstallLease) + ' --timeout-seconds 120';
    if FromSetup then begin
      RecoveryForm := CreateCustomForm(480, 100, True, True);
      RecoveryForm.Caption := '继续卸载星图';
      RecoveryForm.Position := poScreenCenter;
      RecoveryStatus := TNewStaticText.Create(RecoveryForm);
      RecoveryStatus.Parent := RecoveryForm;
      RecoveryStatus.SetBounds(ScaleX(20), ScaleY(24), ScaleX(440), ScaleY(60));
      RecoveryStatus.WordWrap := True;
      RecoveryStatus.Caption := '正在核对本次卸载的剩余项并清理…';
      RecoveryForm.Show;
    end;
    if not RunBoundedUninstallProcess(UninstallRunnerRoot + '\star-map-uninstall-helper.exe',
      Params, UninstallRunnerRoot, 135000, Code) then begin
      if UninstallProcessUncertain then
        RaiseException('尚未确认清理程序停止。现在不要重试或启动星图，请重启电脑后重新打开对应安装包。');
      RaiseException('清理程序已停止，但没有取得完成结果。请关闭本安装的资源任务后重新打开对应安装包。');
    end;
    Detail := UninstallReadText(UninstallResultRoot + '\error.txt') +
      UninstallReadText(UninstallResultRoot + '\remaining.txt');
    if (Code <> 0) or (GetIniString('result', 'status', '', UninstallResultRoot + '\result.ini') <> 'cleared') or
      HasUninstallRecovery then RaiseException('卸载尚未全部完成。' + #13#10 +
        GetIniString('result', 'message', '请保留剩余项并联系星图维护者核对。', UninstallResultRoot + '\result.ini') + #13#10 + Detail);
    Result := True;
    if FromSetup then SuppressibleMsgBox('本次卸载的程序、资料、入口和恢复登记已核对清理完成。', mbInformation, MB_OK, IDOK);
  except
    Log('UNINSTALL_RECOVERY_STOPPED ' + GetExceptionMessage);
    SuppressibleMsgBox(GetExceptionMessage, mbError, MB_OK, IDOK);
  end;
  ReleaseUninstallLease;
  RecoveryStatus := nil;
  if RecoveryForm <> nil then begin
    RecoveryForm.Free;
    RecoveryForm := nil;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := False;
  if not AcquireInstallerMaintenance then Exit;
  if not UninstallSafeAbsolute(ExpandConstant('{app}')) then begin
    SuppressibleMsgBox('安装路径含链接或无法访问，卸载未开始。', mbError, MB_OK, IDOK);
    Exit;
  end;
  if HasUninstallRecovery and not UninstallRecoveryIsPrepared then begin
    SuppressibleMsgBox('资料已经清除或恢复状态尚未核实。请关闭当前窗口，再打开卸载时的同一份安装包，按提示继续清理；不会重新创建资料或资源锁。', mbError, MB_OK, IDOK);
    Exit;
  end;
  if FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
    FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then begin
    SuppressibleMsgBox('上次升级仍有未完成的恢复步骤，请先完成恢复，再进行卸载。资料尚未删除。', mbError, MB_OK, IDOK);
    Exit;
  end;
  { The native ConfirmUninstall message owns the only full-clear confirmation. }
  Result := True;
end;

procedure InitializeUninstallProgressForm;
begin
  UninstallProgressForm.PageNameLabel.Caption := '正在卸载星图';
  UninstallProgressForm.PageDescriptionLabel.Caption := '核对本安装、清除资料，然后移除程序。';
end;

procedure CheckUninstallExecutable(const Path: String);
var Handle: THandle;
begin
  if not UninstallSafeAbsolute(Path) then
    RaiseException('程序路径无法安全确认，请使用对应安装包修复后重试。');
  if not FileExists(Path) then Exit;
  Handle := UninstallCreateFile(Path, $40000000, 0, 0, 3, $80, 0);
  if Handle = THandle(-1) then
    RaiseException('本安装的星图仍在运行、程序文件被占用或无权访问。请退出本安装的星图和资源任务，再点“重试”；不会关闭游戏或其他安装。');
  UninstallCloseHandle(Handle);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Reason, Path: String;
begin
  if CurUninstallStep = usUninstall then begin
    repeat
    try
    if HasUninstallRecovery and not UninstallRecoveryIsPrepared then
      RaiseException('资料已经清除，请关闭当前窗口，再打开卸载时的同一份安装包继续清理。');
    UninstallProgressForm.StatusLabel.Caption := '正在核对本安装和文件占用…';
    CheckUninstallExecutable(ExpandConstant('{app}\{#InstallerExeName}'));
    CheckUninstallExecutable(ExpandConstant('{app}\第五人格小抄助手.exe'));
    AcquireUninstallLease;
    if FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
      FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then
      RaiseException('升级恢复状态发生变化，请先完成恢复；资料尚未删除。');
    CheckUninstallShortcuts;
#ifdef ProductionInstaller
    UninstallProgressForm.StatusLabel.Caption := '正在关闭本安装的游戏启动监听…';
    if not DisableGameWatcher(Reason) then RaiseException(Reason);
#endif
    PrepareUninstallRunner;
    UninstallProgressForm.StatusLabel.Caption := '正在清除本安装的资料，最多等待约两分钟…';
    ClearOwnedInstallation;
    FinishUninstallEntries;
    FinishUninstallData;
#ifdef ProductionInstaller
    CommitGameWatcherUninstall;
#endif
    Break;
    except
      Reason := GetExceptionMessage;
      ReleaseUninstallLease;
      Log('UNINSTALL_STOPPED_BEFORE_NATIVE ' + Reason);
      if UninstallProcessUncertain then begin
        SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
        Abort;
      end;
      if HasUninstallRecovery and not UninstallRecoveryIsPrepared then begin
        SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
        Abort;
      end;
      if UninstallSilent or (SuppressibleMsgBox(Reason + #13#10 +
        '必要的卸载入口已保留。处理原因后可重试，或取消后稍后重新卸载。',
        mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY) then Abort;
    end;
    until False;
    UninstallProgressForm.StatusLabel.Caption := '资料清理完成，正在移除程序和入口…';
  end;
  if CurUninstallStep = usPostUninstall then begin
    { Native failures cannot be rolled back here. Never consume unknown neighbours. }
    Path := ExpandConstant('{app}');
    { The separate registration survives deletion of Inno's own entry. Even
      an apparently empty native tail needs the independent continuation check. }
    UninstallCleanupComplete := ContinueUninstallRecovery(False);
    if not UninstallCleanupComplete then
      SuppressibleMsgBox('卸载尚未全部完成。请关闭相关资源任务后重新打开卸载时的同一份安装包，按提示继续清理；恢复记录会保留。' + #13#10 + Path,
        mbError, MB_OK, IDOK);
  end;
  if CurUninstallStep = usDone then begin
    if UninstallCleanupComplete then
      Log('UNINSTALL_DATA_CLEAR_COMPLETED_NATIVE_ENTRIES_REQUIRE_FINAL_VERIFICATION')
    else SuppressibleMsgBox('卸载清理未完成，请保留剩余项并按前面的错误提示处理。', mbError, MB_OK, IDOK);
  end;
end;

procedure DeinitializeUninstall;
var Reason: String;
begin
  ReleaseUninstallLease;
#ifdef ProductionInstaller
  if UninstallMayHaveChanged or UninstallProcessUncertain then CommitGameWatcherUninstall
  else if not RestoreGameWatcher(Reason) then
    SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
#endif
end;
