{ Shared full uninstall. Upgrades never call this entry.
  usUninstall is the last abortable boundary before native Inno removal.
  https://jrsoftware.org/ishelp/topic_scriptevents.htm }
#ifndef UninstallHelperInventorySHA256
  #error UninstallHelperInventorySHA256 is required; perform a full helper freeze
#endif
var
  UninstallLease: THandle;
  UninstallRunnerRoot, UninstallResultRoot: String;
  UninstallMayHaveChanged, UninstallCleanupComplete: Boolean;

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
var Key, Previous: String; Root, View: Integer;
begin
  RemoveOwnedUninstallShortcut(ExpandConstant('{group}\{#InstallerAppName}.lnk'));
  RemoveOwnedUninstallShortcut(ExpandConstant('{autodesktop}\{#InstallerAppName}.lnk'));
#ifdef ProductionInstaller
  RemoveOwnedUninstallShortcut(ExpandConstant('{group}\卸载星图.lnk'));
  RemoveOwnedUninstallShortcut(ExpandConstant('{group}\卸载星图（保留个人资料）.lnk'));
  RemoveOwnedUninstallShortcut(ExpandConstant('{userdesktop}\第五人格小抄助手.lnk'));
  RemoveOwnedUninstallShortcut(ExpandConstant('{userprograms}\第五人格小抄助手\第五人格小抄助手.lnk'));
#endif
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    ExpandConstant('{#InstallerAppId}_is1');
  for View := 0 to 1 do begin
#ifdef ProductionInstaller
    if View = 0 then Root := HKCU64 else Root := HKCU32;
#else
    if View = 0 then Root := HKLM64 else Root := HKLM32;
#endif
    if RegKeyExists(Root, Key) then begin
      if not RegQueryStringValue(Root, Key, 'InstallLocation', Previous) or
        (CompareText(InstallerNormalizedDirectory(Previous),
          InstallerNormalizedDirectory(ExpandConstant('{app}'))) <> 0) then
        RaiseException('卸载登记现在指向其他位置，未清理未知登记。');
      if not RegDeleteKeyIncludingSubkeys(Root, Key) then
        RaiseException('本次安装的卸载登记未能清除，请检查权限后重试。');
    end;
  end;
end;

procedure ReleaseUninstallLease;
begin
  if (UninstallLease <> 0) and (UninstallLease <> THandle(-1)) then
    UninstallCloseHandle(UninstallLease);
  UninstallLease := 0;
end;

procedure AcquireUninstallLease;
var Path: String;
begin
  Path := ExpandConstant('{app}\work\map-packages\import.lock');
  if not UninstallSafeAbsolute(Path) or not ForceDirectories(ExtractFileDir(Path)) then
    RaiseException('安装路径含链接或无法访问，尚未开始卸载。');
  UninstallLease := UninstallCreateFile(Path, $C0010000, 0, 0, 4, $80, 0);
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
  UninstallRunnerRoot := ExpandConstant('{tmp}\xingtu-uninstall-runner');
  UninstallResultRoot := ExpandConstant('{tmp}\xingtu-uninstall-result');
  if not UninstallSafeAbsolute(UninstallRunnerRoot) or
    not UninstallSafeAbsolute(UninstallResultRoot) or
    not CreateDir(UninstallRunnerRoot) or not CreateDir(UninstallResultRoot) then
    RaiseException('无法创建独立的卸载检查目录，尚未删除资料。');
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
    ' --lock-handle ' + IntToStr(UninstallLease);
  Code := -1;
  UninstallMayHaveChanged := True;
  if not Exec(UninstallRunnerRoot + '\star-map-uninstall-helper.exe', Params,
    UninstallRunnerRoot, SW_HIDE, ewWaitUntilTerminated, Code) then
    RaiseException('卸载检查程序未完成，不能确认资料清理结果。请保留安装目录并修复后重试。');
  Receipt := UninstallResultRoot + '\result.ini';
  if (GetIniString('result', 'status', '', Receipt) = 'error') and
    (GetIniString('result', 'mutationStarted', '', Receipt) = 'False') then
    UninstallMayHaveChanged := False;
  if (Code <> 0) or (GetIniString('result', 'status', '', Receipt) <> 'cleared') then begin
    Detail := UninstallReadText(UninstallResultRoot + '\error.txt') +
      UninstallReadText(UninstallResultRoot + '\remaining.txt');
    Log('UNINSTALL_FULL_CLEAR_FAILED ' + Detail);
    if UninstallSafeAbsolute(ExpandConstant('{app}\.xingtu-uninstall-failed.txt')) then
      SaveStringToFile(ExpandConstant('{app}\.xingtu-uninstall-failed.txt'), Detail, False);
    RaiseException('资料没有全部清除，Windows 程序卸载尚未开始。已删除的部分不能自动撤回；请按剩余项处理后重试。' + #13#10 + Detail);
  end;
  Log('UNINSTALL_OWNED_DATA_CLEARED_BEFORE_NATIVE');
end;

function InitializeUninstall: Boolean;
begin
  Result := False;
  if not UninstallSafeAbsolute(ExpandConstant('{app}')) then begin
    SuppressibleMsgBox('安装路径含链接或无法访问，卸载未开始。', mbError, MB_OK, IDOK);
    Exit;
  end;
  if FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
    FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then begin
    SuppressibleMsgBox('上次升级仍有未完成的恢复步骤，请先完成恢复，再进行卸载。资料尚未删除。', mbError, MB_OK, IDOK);
    Exit;
  end;
  if not UninstallSilent then
    if MsgBox('卸载将永久清除本次安装的程序、设置、缓存、图包、自建路线图库及迁移备份，不经过回收站。' + #13#10 +
      '已确认属于本次安装的外置资料也会清除。其他实例和外部原始文件不在范围内。' + #13#10 +
      '需要保留资料时请取消卸载；更新版本请运行安装包的升级入口。继续卸载？',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then Exit;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Reason, Path: String; Disposition: Byte;
begin
  if CurUninstallStep = usUninstall then begin
    AcquireUninstallLease;
    if FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
      FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then
      RaiseException('升级恢复状态发生变化，请先完成恢复；资料尚未删除。');
    CheckUninstallShortcuts;
#ifdef ProductionInstaller
    if not InstalledExecutableAvailable then RaiseException('星图仍在运行或程序文件被占用，请退出后重试。');
    if not DisableGameWatcher(Reason) then RaiseException(Reason);
#endif
    PrepareUninstallRunner;
    ClearOwnedInstallation;
#ifdef ProductionInstaller
    CommitGameWatcherUninstall;
#endif
  end;
  if CurUninstallStep = usPostUninstall then begin
    FinishUninstallEntries;
    Disposition := 1;
    if (UninstallLease = 0) or not UninstallDeleteByHandle(UninstallLease, 4, Disposition, 1) then
      RaiseException('资料清理没有完成，剩余资源锁文件：' + ExpandConstant('{app}\work\map-packages\import.lock'));
    ReleaseUninstallLease;
    Path := ExpandConstant('{app}\work\map-packages\import.lock');
    if not UninstallSafeAbsolute(Path) or FileExists(Path) then
      RaiseException('资料清理没有完成，剩余资源锁文件：' + Path);
    if DirExists(ExtractFileDir(Path)) and not RemoveDir(ExtractFileDir(Path)) then
      RaiseException('资料清理没有完成，剩余目录：' + ExtractFileDir(Path));
    Path := ExpandConstant('{app}\work');
    if not UninstallSafeAbsolute(Path) or (DirExists(Path) and not RemoveDir(Path)) then
      RaiseException('资料清理没有完成，剩余目录：' + Path);
    { Native removal already tried the install root while our locked work tree existed.
      Retry only the empty install root; never consume later/unknown neighbours. }
    Path := ExpandConstant('{app}');
    if not UninstallSafeAbsolute(Path) or FileExists(Path) or
      (DirExists(Path) and not RemoveDir(Path)) then
      RaiseException('安装目录仍有剩余项或无法移除：' + Path);
    UninstallCleanupComplete := True;
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
  if UninstallMayHaveChanged then CommitGameWatcherUninstall
  else if not RestoreGameWatcher(Reason) then
    SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
#endif
end;
