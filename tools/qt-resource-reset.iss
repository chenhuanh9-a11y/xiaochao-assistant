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
#ifndef ResetHelperInventorySHA256
  #error ResetHelperInventorySHA256 is required
#endif
var
  ResourceResetRoot, ResourceResetExtractRoot, ResourceResetSession: String;
  ResourceResetPlanHash, ResourceResetTransaction, ResourceResetLastStatus: String;
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
      AddQuotes(ResourceResetExtractRoot + '\_internal\managed-base-resources');
    if ResourceResetPlanHash <> '' then Params := Params + ' --plan-sha256 ' + ResourceResetPlanHash;
    if ResourceResetTransaction <> '' then Params := Params + ' --transaction-id ' + AddQuotes(ResourceResetTransaction);
    if Confirmed then Params := Params + ' --confirmed';
    Code := -1;
    Log('RESOURCE_RESET_STEP ' + Action + ' plan=' + ResourceResetPlanHash + ' transaction=' + ResourceResetTransaction);
    if not Exec(ResourceResetExtractRoot + '\star-map-reset-helper.exe', Params,
      ResourceResetExtractRoot, SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
      Reason := '资源升级步骤 ' + Action + ' 未完成（错误码 ' + IntToStr(Code) + '）。' + #13#10 +
        ResetReadText(ResultDir + '\error.txt');
      Log(Reason);
      Exit;
    end;
    if GetIniString('result', 'action', '', ResultDir + '\result.ini') <> Action then
      RaiseException('资源升级回执不完整，不能据此认定成功。');
    ResourceResetLastStatus := GetIniString('result', 'status', '', ResultDir + '\result.ini');
    Log('RESOURCE_RESET_RESULT ' + Action + ' status=' + ResourceResetLastStatus);
    Result := True;
  except
    Reason := GetExceptionMessage;
    Log('RESOURCE_RESET_ERROR ' + Action + ' ' + Reason);
  end;
end;

function ReviewResourceReset(var Reason: String): Boolean;
var ResultDir, Summary: String; Id: String; HasPending: Boolean;
begin
  Result := False;
  Reason := '';
  if ResourceResetReviewed then begin
    Result := CompareText(ResourceResetRoot, ExpandConstant('{app}')) = 0;
    if not Result then Reason := '安装目录已变化，请退出后重新盘点。';
    Exit;
  end;
  HasPending := FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json'));
  { Fresh installs do not reset retained user data merely because a work folder exists. }
  if not HasPending and not FileExists(ExpandConstant('{app}\') + OwnershipName) and
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
    ExtractTemporaryFiles('{app}\star-map-reset-helper.exe');
    ExtractTemporaryFiles('{app}\_star_map_reset_runtime\*');
    ExtractTemporaryFiles('{app}\release-report.json');
    ExtractTemporaryFiles('{app}\_internal\managed-base-resources\*');
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
        (ResourceResetLastStatus <> 'committed') then Exit;
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
  Result := (ResourceResetLastStatus = 'prepared-resources') and (Length(Id) = 36);
  if Result then for I := 1 to Length(Id) do
    if Pos(Id[I], '0123456789abcdef-') = 0 then Result := False;
  if not Result then begin Reason := '资源准备回执无法核实，必须恢复后重试。'; Exit; end;
  ResourceResetTransaction := Id;
end;

function ResolveResourceResetCommit(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetCommitAttempted or ResourceResetFinalized then Exit;
  if not CallResourceReset('status', False, Reason, ResultDir) then begin
    ResourceResetOutcomeUnknown := True; Result := False; Exit;
  end;
  ResourceResetOutcomeUnknown := False;
  if ResourceResetLastStatus = 'committed' then begin
    { Durable commit forbids either half rolling back, but is not proof that
      the pending marker was safely cleared. Retry the idempotent finalizer. }
    ResourceResetCommitted := True;
    Result := CallResourceReset('commit', False, Reason, ResultDir) and
      (ResourceResetLastStatus = 'committed');
    ResourceResetFinalized := Result;
    if not Result then Reason := '程序和资源已提交，但恢复标记尚未安全清理，暂不能启动。请保留隔离区并核对恢复。' + #13#10 + Reason;
  end
  else if ResourceResetLastStatus <> 'prepared-resources' then begin
    ResourceResetOutcomeUnknown := True;
    Reason := '资源提交状态异常；保留程序、隔离区和恢复记录，不能猜测回滚。'; Result := False;
  end;
end;

function CommitResourceReset(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted then Exit;
  ResourceResetCommitAttempted := True;
  Result := CallResourceReset('commit', False, Reason, ResultDir) and
    (ResourceResetLastStatus = 'committed');
  ResourceResetCommitted := Result;
  ResourceResetFinalized := Result;
  if not Result then begin
    ResolveResourceResetCommit(Reason);
    Result := ResourceResetFinalized;
    if Reason = '' then Reason := '资源升级没有提交，安装将尝试恢复旧程序和资源。';
  end;
end;

function RollbackResourceReset(var Reason: String): Boolean;
var ResultDir: String;
begin
  Result := True;
  if not ResourceResetStarted or ResourceResetCommitted then Exit;
  if ResourceResetOutcomeUnknown then begin Result := False; Exit; end;
  Result := CallResourceReset('rollback', False, Reason, ResultDir) and
    ((ResourceResetLastStatus = 'rolled-back') or (ResourceResetLastStatus = 'not-needed'));
  if not Result and (Reason = '') then Reason := '资源尚未完整恢复，请保留 work/resource-upgrades 和 pending-resource-reset.json。';
end;
