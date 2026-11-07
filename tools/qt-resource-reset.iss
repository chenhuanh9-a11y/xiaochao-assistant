(* Current-package helper, exact reviewed plan, and one continuous Setup lease.
  ExtractTemporaryFiles keeps literal {app} beneath {tmp}; it never runs the
  old installation helper. https://jrsoftware.org/ishelp/topic_isxfunc_extracttemporaryfiles.htm *)
#ifndef ResetSourceCommit
  #error ResetSourceCommit is required
#endif
#ifndef ResetTargetReportSHA256
  #error ResetTargetReportSHA256 is required
#endif
#ifndef ResetCandidateSHA256
  #error ResetCandidateSHA256 is required
#endif
#ifndef ResetProgramInventorySha256
  #error ResetProgramInventorySha256 is required
#endif
#ifndef ResetHelperInventorySHA256
  #error ResetHelperInventorySHA256 is required
#endif
var
  ResourceResetRoot, ResourceResetExtractRoot, ResourceResetSession: String;
  ResourceResetPlanHash, ResourceResetTransaction, ResourceResetLastStatus, ResourceResetSessionId: String;
  ResourceResetInstallerStatus: String;
  ResourceResetNativeStarted, ResourceResetNativeCompleted: Boolean;
  ResourceResetReviewed, ResourceResetStarted, ResourceResetCommitted, ResourceResetFinalized: Boolean;
  ResourceResetCommitAttempted, ResourceResetOutcomeUnknown: Boolean;
  ResourceResetCallNumber: Integer;
  ResourceResetFailureCode: Integer;
  ResourceResetNativeMetadataNeedsRepair: Boolean;

function ResetGetCurrentProcessId: LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function ResetGetCurrentProcess: THandle;
  external 'GetCurrentProcess@kernel32.dll stdcall';
function ResetGetProcessTimes(Handle: THandle; var Created, Exited, Kernel, User: Int64): Boolean;
  external 'GetProcessTimes@kernel32.dll stdcall';
function ResetGetModuleName(Module: THandle; Buffer: String; Size: LongWord): LongWord;
  external 'GetModuleFileNameW@kernel32.dll stdcall';

function ResetSafePath(Path: String): Boolean;
var Parent: String; Attributes: LongWord;
begin
  Result := False;
  repeat
    Attributes := InstallerFileAttributes(Path);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then Exit;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Break;
    Path := Parent;
  until Path = '';
  Result := True;
end;

function ResetHex(const Value: String; Size: Integer): Boolean;
var I: Integer;
begin
  Result := False;
  if Length(Value) <> Size then Exit;
  for I := 1 to Size do
    if Pos(Value[I], '0123456789abcdef') = 0 then Exit;
  Result := True;
end;

function ResetReadText(const Path: String): String;
var Lines: TArrayOfString; I: Integer;
begin
  Result := '';
  if LoadStringsFromFile(Path, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      Result := Result + Lines[I] + #13#10;
end;

procedure VerifyResetInputs;
var Lines: TArrayOfString; I: Integer; Relative, Path, Inventory: String;
begin
  Inventory := ExpandConstant('{tmp}\reset-helper-inventory.txt');
  if GetSHA256OfFile(ExpandConstant('{tmp}\reset-program-inventory.json')) <> '{#ResetProgramInventorySha256}' then
    RaiseException('当前安装包的完整程序库存无法验证。');
  if not ResetSafePath(ResourceResetExtractRoot) or
    (GetSHA256OfFile(Inventory) <> '{#ResetHelperInventorySHA256}') or
    not LoadStringsFromFile(Inventory, Lines) or (GetArrayLength(Lines) = 0) then
    RaiseException('升级 helper 清单无法验证，未开始资源替换。');
  for I := 0 to GetArrayLength(Lines) - 1 do begin
    if (Length(Lines[I]) < 66) or not ResetHex(Copy(Lines[I], 1, 64), 64) or
      (Copy(Lines[I], 65, 1) <> ' ') then RaiseException('升级 helper 清单无效。');
    Relative := Copy(Lines[I], 66, Length(Lines[I]));
    if (Pos('..', Relative) > 0) or (Pos(':', Relative) > 0) or
      (Pos('/', Relative) > 0) or (Relative[1] = '\') then
      RaiseException('升级 helper 路径无效。');
    Path := AddBackslash(ResourceResetExtractRoot) + Relative;
    if not ResetSafePath(Path) or (GetSHA256OfFile(Path) <> Copy(Lines[I], 1, 64)) then
      RaiseException('升级 helper 文件已变化，未执行。');
  end;
  if (GetSHA256OfFile(ResourceResetExtractRoot + '\release-report.json') <> '{#ResetTargetReportSHA256}') or
    (GetSHA256OfFile(ResourceResetExtractRoot + '\_internal\managed-base-resources\manifest.json') <> '{#ResetCandidateSHA256}') then
    RaiseException('当前安装包的程序或资源身份无法验证。');
end;

function CallResourceReset(const Action: String; Confirmed: Boolean; var Reason, ResultDir: String): Boolean;
var Params, ParentImage: String; Code: Integer; Size: LongWord;
  Created, Exited, KernelTime, UserTime: Int64;
begin
  Result := False;
  Reason := '';
  ResourceResetLastStatus := '';
  try
    if (ResourceLock = 0) or (CompareText(ResourceLockRoot, ResourceResetRoot) <> 0) or
      (CompareText(ExpandConstant('{app}'), ResourceResetRoot) <> 0) then
      RaiseException('资源锁或安装目录已变化，请退出安装后重新核对。');
    VerifyResetInputs;
    SetLength(ParentImage, 32768);
    Size := ResetGetModuleName(0, ParentImage, 32768);
    if (Size = 0) or (Size >= 32768) or not ResetGetProcessTimes(ResetGetCurrentProcess,
      Created, Exited, KernelTime, UserTime) then RaiseException('无法核实当前安装器进程。');
    SetLength(ParentImage, Size);
    ResourceResetCallNumber := ResourceResetCallNumber + 1;
    ResultDir := ResourceResetSession + '\operation-' + IntToStr(ResourceResetCallNumber);
    if not CreateDir(ResultDir) then RaiseException('无法创建独立的升级回执目录。');
    Params := '--action ' + Action + ' --install-root ' + AddQuotes(ResourceResetRoot) +
      ' --result-directory ' + AddQuotes(ResultDir) +
      ' --target-report ' + AddQuotes(ResourceResetExtractRoot + '\release-report.json') +
      ' --target-report-sha256 {#ResetTargetReportSHA256} --source-commit {#ResetSourceCommit}' +
      ' --display-version {#DisplayVersion} --numeric-version {#NumericVersion}' +
      ' --parent-pid ' + IntToStr(ResetGetCurrentProcessId) + ' --parent-created ' + IntToStr(Created) +
      ' --parent-executable ' + AddQuotes(ParentImage) + ' --parent-sha256 ' + GetSHA256OfFile(ParentImage) +
      ' --lock-handle ' + IntToStr(ResourceLock) + ' --candidate-directory ' +
      AddQuotes(ResourceResetExtractRoot + '\_internal\managed-base-resources') +
      ' --program-inventory ' + AddQuotes(ExpandConstant('{tmp}\reset-program-inventory.json')) +
      ' --program-inventory-sha256 {#ResetProgramInventorySha256}' +
      ' --uninstall-exe ' + AddQuotes(ExpandConstant('{uninstallexe}')) +
      ' --group-shortcut ' + AddQuotes(ExpandConstant('{group}\星图.lnk')) +
      ' --uninstall-shortcut ' + AddQuotes(ExpandConstant('{group}\卸载星图.lnk')) +
      ' --desktop-shortcut ' + AddQuotes(ExpandConstant('{autodesktop}\星图.lnk'));
    if not WizardNoIcons then Params := Params + ' --group-enabled';
    if CreateDesktopShortcut then Params := Params + ' --desktop-enabled';
    if GameWatcherWasEnabled then Params := Params + ' --restore-watcher';
    if ResourceResetSessionId <> '' then Params := Params + ' --session-id ' + AddQuotes(ResourceResetSessionId);
    if ResourceResetPlanHash <> '' then Params := Params + ' --plan-sha256 ' + ResourceResetPlanHash;
    if ResourceResetTransaction <> '' then Params := Params + ' --transaction-id ' + AddQuotes(ResourceResetTransaction);
    if Confirmed then Params := Params + ' --confirmed';
    Code := -1;
    Log('RESOURCE_RESET_STEP ' + Action + ' plan=' + ResourceResetPlanHash + ' transaction=' + ResourceResetTransaction);
    if not Exec(ResourceResetExtractRoot + '\star-map-reset-helper.exe', Params,
      ResourceResetExtractRoot, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
      Reason := '资源升级步骤 ' + Action + ' 未完成（错误码 ' + IntToStr(Code) + '）。' + #13#10 +
        ResetReadText(ResultDir + '\error.txt');
      if ((Action = 'prepare') or (Action = 'start-native')) and
        (Pos('native-observation-evidence-budget-exceeded', Reason) > 0) then
        Reason := '本包在当前安装路径下的完整恢复凭据超出支持容量，尚未进入 Windows 程序安装。将核对恢复准备步骤，暂不能继续。' + #13#10 + Reason;
      Log(Reason);
      Exit;
    end;
    if GetIniString('result', 'action', '', ResultDir + '\result.ini') <> Action then
      RaiseException('资源升级回执不完整，不能据此认定成功。');
    ResourceResetLastStatus := GetIniString('result', 'status', '', ResultDir + '\result.ini');
    ResourceResetInstallerStatus := GetIniString('result', 'installerStatus', '', ResultDir + '\result.ini');
    if (ResourceResetInstallerStatus = 'native-in-progress') or
      (ResourceResetInstallerStatus = 'native-complete') or
      (ResourceResetInstallerStatus = 'completed') then ResourceResetNativeStarted := True;
    if (ResourceResetInstallerStatus = 'native-complete') or
      (ResourceResetInstallerStatus = 'completed') then ResourceResetNativeCompleted := True;
    Log('RESOURCE_RESET_RESULT ' + Action + ' status=' + ResourceResetLastStatus);
    Result := True;
  except
    Reason := GetExceptionMessage;
    Log('RESOURCE_RESET_ERROR ' + Action + ' ' + Reason);
  end;
end;

function ReviewResourceReset(var Reason: String): Boolean;
var ResultDir, Summary: String; Id: String; HasPending, HasInstallerPending: Boolean;
begin
  Result := False;
  Reason := '';
  if ResourceResetReviewed then begin
    Result := CompareText(ResourceResetRoot, ExpandConstant('{app}')) = 0;
    if not Result then Reason := '安装目录已变化，请退出后重新盘点。';
    Exit;
  end;
  HasPending := FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json'));
  HasInstallerPending := FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json'));
  { Fresh installs do not reset retained user data merely because a work folder exists. }
  if not HasPending and not HasInstallerPending and not FileExists(ExpandConstant('{app}\') + OwnershipName) and
    not FileExists(ExpandConstant('{app}\星图.exe')) and
    not FileExists(ExpandConstant('{app}\第五人格小抄助手.exe')) then begin Result := True; Exit; end;
  if WizardSilent then begin
    Reason := '覆盖升级需要先核对资源清理清单，请使用交互安装。静默安装不会自动同意清理。'; Exit;
  end;
  ResourceResetRoot := ExpandConstant('{app}');
  ResourceResetPlanHash := '';
  ResourceResetTransaction := '';
  ResourceResetExtractRoot := ExpandConstant('{tmp}\') + '{app}';
  try
    if ResourceResetSession = '' then begin
      ResourceResetSession := ExpandConstant('{tmp}\resource-reset-session');
      if not CreateDir(ResourceResetSession) then RaiseException('资源升级会话目录已存在，请重新运行安装包。');
    end;
    ExtractTemporaryFile('reset-helper-inventory.txt');
    ExtractTemporaryFile('reset-program-inventory.json');
    ExtractTemporaryFiles('{app}\star-map-reset-helper.exe');
    ExtractTemporaryFiles('{app}\_star_map_reset_runtime\*');
    ExtractTemporaryFiles('{app}\release-report.json');
    ExtractTemporaryFiles('{app}\_internal\managed-base-resources\*');
    if HasInstallerPending then begin
      if not CallResourceReset('installer-pending', False, Reason, ResultDir) then Exit;
      ResourceResetSessionId := GetIniString('result', 'sessionId', '', ResultDir + '\result.ini');
      ResourceResetPlanHash := GetIniString('result', 'planSha256', '', ResultDir + '\result.ini');
      ResourceResetTransaction := GetIniString('result', 'transactionId', '', ResultDir + '\result.ini');
      if (ResourceResetInstallerStatus <> 'native-complete') and
        (ResourceResetInstallerStatus <> 'completed') then begin
        Reason := '上次升级没有完整的原生安装完成凭据。本次不会猜测回退，也不会启用程序；请保留恢复记录并先核对修复。'; Exit;
      end;
      if MsgBox('上次 Windows 安装已保存完成凭据，但整体升级尚未结束。是否重新核验同一账号、程序库存、安装登记、卸载器和快捷方式，并在全部仍匹配时完成资源提交？' + #13#10 +
        '不会覆盖后来的变化，不会恢复旧资源或删除隔离历史。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then begin
        Reason := '已保留未完成升级，暂不能启动程序。'; Exit;
      end;
      if not CallResourceReset('resume-native-complete', True, Reason, ResultDir) or
        (GetIniString('result', 'installerFinalized', '', ResultDir + '\result.ini') <> 'True') or
        FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
        FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then begin
        if Reason = '' then Reason := '上次升级仍有未完成的恢复记录，不能继续本次安装。';
        Exit;
      end;
      { Only a verified finalized old session may restore its original opt-in. }
      if GetIniString('result', 'restoreWatcher', 'False', ResultDir + '\result.ini') = 'True' then begin
        GameWatcherOriginalDir := ResourceResetRoot;
        GameWatcherRestorePending := True;
        if not RestoreGameWatcher(Reason) then Exit;
      end;
      GameWatcherCaptured := False;
      if not DisableGameWatcher(Reason) then Exit;
      ResourceResetSessionId := '';
      ResourceResetPlanHash := '';
      ResourceResetTransaction := '';
      ResourceResetNativeStarted := False;
      ResourceResetNativeCompleted := False;
      HasPending := False;
    end;
    if HasPending then begin
      if not CallResourceReset('pending', False, Reason, ResultDir) then Exit;
      if ResourceResetLastStatus <> 'committed' then begin
        Reason := '上次升级未提交，请先核对恢复原程序与资源；本次安装不会只恢复其中一半。'; Exit;
      end;
      ResourceResetPlanHash := GetIniString('result', 'planSha256', '', ResultDir + '\result.ini');
      ResourceResetTransaction := GetIniString('result', 'transactionId', '', ResultDir + '\result.ini');
      if not ResetHex(ResourceResetPlanHash, 64) or (Length(ResourceResetTransaction) <> 36) then
        RaiseException('上次升级状态无法核实。');
      if MsgBox('发现上次已提交升级遗留的恢复标记。是否核对原目标程序身份并完成标记清理？' + #13#10 +
        '事务：' + ResourceResetTransaction + #13#10 + '不会恢复旧资源，也不会删除隔离区。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then begin
        Reason := '已保留上次升级的恢复记录，本次安装停止。'; Exit;
      end;
      if not CallResourceReset('resume-committed', True, Reason, ResultDir) or
        (ResourceResetLastStatus <> 'committed') then begin
        if Reason = '' then Reason := '上次资源提交状态无法核实。';
        Exit;
      end;
      ResourceResetPlanHash := '';
      ResourceResetTransaction := '';
    end;
    if not CallResourceReset('inspect', False, Reason, ResultDir) then Exit;
    if ResourceResetLastStatus <> 'inspected' then RaiseException('资源盘点结果无效。');
    Id := GetIniString('result', 'planSha256', '', ResultDir + '\result.ini');
    if not ResetHex(Id, 64) then RaiseException('资源盘点摘要无效。');
    Summary := ResetReadText(ResultDir + '\summary.txt');
    if Summary = '' then RaiseException('无法读取资源盘点，请停止安装。');
    if MsgBox(Summary, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then begin
      Reason := '未确认资源清理，本次覆盖升级已停止；原资源未改动。'; Exit;
    end;
    ResourceResetPlanHash := Id;
    ResourceResetSessionId := GetIniString('result', 'sessionId', '', ResultDir + '\result.ini');
    if Length(ResourceResetSessionId) <> 36 then RaiseException('升级会话身份无效。');
    ResourceResetReviewed := True;
    Result := True;
  except
    Reason := GetExceptionMessage;
  end;
end;

function PrepareResourceReset(var Reason: String): Boolean;
var ResultDir, Id: String; I: Integer;
begin
  Result := True;
  if not ResourceResetReviewed then Exit;
  ResourceResetStarted := True; { Even a lost result must attempt validated recovery. }
  Result := CallResourceReset('prepare', True, Reason, ResultDir);
  if not Result then Exit;
  Id := GetIniString('result', 'transactionId', '', ResultDir + '\result.ini');
  Result := (ResourceResetLastStatus = 'prepared') and
    (GetIniString('result', 'resourceStatus', '', ResultDir + '\result.ini') = 'prepared-resources') and (Length(Id) = 36);
  if Result then for I := 1 to Length(Id) do
    if Pos(Id[I], '0123456789abcdef-') = 0 then Result := False;
  if not Result then begin Reason := '资源准备回执无法核实，必须恢复后重试。'; Exit; end;
  ResourceResetTransaction := Id;
end;

function StartNativeResourceReset(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted then Exit;
  { A failed/absent receipt may still mean the durable boundary was crossed. }
  ResourceResetOutcomeUnknown := True;
  Result := CallResourceReset('start-native', False, Reason, ResultDir) and
    (ResourceResetInstallerStatus = 'native-in-progress');
  if Result then begin
    ResourceResetNativeStarted := True;
    ResourceResetOutcomeUnknown := False;
  end;
end;

function CompleteNativeResourceReset(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted then Exit;
  { The memory boundary never moves backward, even if durable evidence fails. }
  ResourceResetNativeCompleted := True;
  Result := CallResourceReset('native-complete', False, Reason, ResultDir) and
    (ResourceResetInstallerStatus = 'native-complete');
end;

function ResolveResourceResetCommit(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted or ResourceResetFinalized then Exit;
  if not CallResourceReset('status', False, Reason, ResultDir) then begin
    ResourceResetOutcomeUnknown := True; Result := False; Exit;
  end;
  ResourceResetOutcomeUnknown := False;
  ResourceResetCommitted := GetIniString('result', 'resourceCommitted', 'False', ResultDir + '\result.ini') = 'True';
  ResourceResetFinalized := GetIniString('result', 'installerFinalized', 'False', ResultDir + '\result.ini') = 'True';
  if ResourceResetFinalized then Exit;
  if (ResourceResetInstallerStatus = 'native-complete') or
    (ResourceResetInstallerStatus = 'completed') then begin
    Result := CallResourceReset('commit', False, Reason, ResultDir) and
      (GetIniString('result', 'installerFinalized', 'False', ResultDir + '\result.ini') = 'True');
    ResourceResetFinalized := Result;
    if Result then ResourceResetCommitted := True;
  end else if ResourceResetNativeStarted or ResourceResetNativeCompleted then begin
    Reason := '原生安装已开始，但没有可验证的完整完成凭据。不能安全回退或启用；保留全部恢复证据。';
    Result := False;
  end else if (ResourceResetInstallerStatus <> 'prepared') and
    (ResourceResetInstallerStatus <> 'aborted') and (ResourceResetLastStatus <> 'not-needed') then begin
    ResourceResetOutcomeUnknown := True;
    Reason := '升级状态无法核实，未执行回退或启用。'; Result := False;
  end;
end;

function CommitResourceReset(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted then Exit;
  ResourceResetCommitAttempted := True;
  Result := CallResourceReset('commit', False, Reason, ResultDir) and
    (GetIniString('result', 'installerFinalized', 'False', ResultDir + '\result.ini') = 'True');
  ResourceResetFinalized := Result;
  ResourceResetCommitted := Result;
  if not Result then begin
    ResolveResourceResetCommit(Reason);
    Result := ResourceResetFinalized;
    if Reason = '' then Reason := '整体升级尚未核验提交，保留恢复记录，不会自动回退旧文件。';
  end;
end;

function RollbackResourceReset(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted or ResourceResetFinalized then Exit;
  if ResourceResetOutcomeUnknown or ResourceResetNativeStarted or ResourceResetNativeCompleted then begin
    Reason := '已跨过或无法确认原生安装边界，禁止单独回退资源。'; Result := False; Exit;
  end;
  Result := CallResourceReset('rollback', False, Reason, ResultDir) and
    ((ResourceResetLastStatus = 'aborted') or (ResourceResetLastStatus = 'not-needed')) and
    not FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) and
    not FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json'));
  if not Result and (Reason = '') then Reason := '安装前准备尚未完整恢复，请保留全部恢复记录。';
end;
