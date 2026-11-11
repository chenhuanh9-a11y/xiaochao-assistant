{ Parent-leased production migration. Historical QtIFW tools are never run. }
var
  LegacySourcePage: TInputDirWizardPage;
  LegacyMigrationPrepared, LegacyNativeStarted, LegacyAssetsCommitted: Boolean;

procedure InitializeWizard;
begin
  LegacySourcePage := CreateInputDirPage(wpSelectDir, '旧版资料迁移',
    '从旧版升级时，请选择原安装目录；全新安装可留空。',
    '设置和自建路线将复制到新的空安装目录。旧地图资源整套隔离保存，需要按当前规则重新导入。' + #13#10 +
    '旧版维护入口暂不退出，原资料保留；本草稿不会自动启动程序，也不代表完整升级验收。', False, '');
  LegacySourcePage.Add('原安装目录（全新安装留空）：');
end;

function LegacyMigrationSelected: Boolean;
begin
  Result := Trim(LegacySourcePage.Values[0]) <> '';
end;

function LegacyCompletionText: String;
begin
  if LegacyAssetsCommitted then
    Result := '新程序安装及资料副本已核实。已复制的旧地图资源保持隔离，缺少的基础包及旧格式图包需要重新导入；旧版维护入口尚未退出，整体升级未完成。'
  else Result := '资料迁移尚未完成，不能启动星图。请保留新旧目录和恢复记录，再运行同一安装包续办。';
end;

function PrepareLegacyMigration: String;
var ResultDir: String;
begin
  Result := '';
  if not LegacyMigrationSelected then Exit;
  LegacySourceRoot := Trim(LegacySourcePage.Values[0]);
  if InstallerDirectoriesOverlap(InstallerNormalizedDirectory(LegacySourceRoot),
      InstallerNormalizedDirectory(ExpandConstant('{app}'))) then begin
    Result := '旧版迁移需要另一个独立目录，原资料不会覆盖。'; Exit;
  end;
  try
    ResourceResetRoot := ExpandConstant('{app}');
    if ResourceResetSession = '' then begin
      ResourceResetSession := ExpandConstant('{tmp}\legacy-handoff-session');
      if not CreateDir(ResourceResetSession) then RaiseException('无法创建独立迁移回执目录。');
    end;
    ExtractAndVerifyResetHelper;
    if FileExists(ResourceResetRoot + '\.legacy-upgrade-prepared\handoff-plan.json') and
      FileExists(ResourceResetRoot + '\work\pending-installer-upgrade.json') then begin
      if not CallResourceReset('legacy-status', False, Result, ResultDir) then Exit;
    end else if not CallResourceReset('legacy-prepare', True, Result, ResultDir) then Exit;
    ResourceResetSessionId := GetIniString('result', 'sessionId', '', ResultDir + '\result.ini');
    ResourceResetPlanHash := GetIniString('result', 'planSha256', '', ResultDir + '\result.ini');
    if (Length(ResourceResetSessionId) <> 36) or not ResetHex(ResourceResetPlanHash, 64) then
      RaiseException('迁移会话或完整清单无法核实。');
    if ResourceResetInstallerStatus = 'prepared' then begin
      if not CallResourceReset('legacy-prepare', True, Result, ResultDir) then Exit;
      LegacyMigrationPrepared := True;
      Exit;
    end;
    if (ResourceResetInstallerStatus = 'native-in-progress') or
      (ResourceResetInstallerStatus = 'native-complete') then begin
      { Return a reason after recovery so Inno cannot install native files twice. }
      if CallResourceReset('legacy-resume', False, Result, ResultDir) then begin
        LegacyAssetsCommitted := GetIniString('result', 'migrationCommitted', '', ResultDir + '\result.ini') = 'True';
        Result := LegacyCompletionText;
      end;
      if Result = '' then Result := '上次安装结果尚未核实，保留现场，不能再次安装原生程序。';
      Exit;
    end;
    Result := '该迁移已取消或状态不允许安装。原件与准备副本保留，请核对恢复记录。';
  except
    Result := GetExceptionMessage;
  end;
end;

function StartLegacyNative(var Reason: String): Boolean;
var ResultDir: String;
begin
  LegacyNativeStarted := True;
  ResourceResetFailureCode := 64;
  Result := CallResourceReset('legacy-start-native', False, Reason, ResultDir) and
    (ResourceResetInstallerStatus = 'native-in-progress');
  if not Result and (Reason = '') then Reason := '原生安装准备尚未核实，不能重复启动。';
end;

procedure CompleteLegacyNative(var Reason: String);
var ResultDir: String;
begin
  ResourceResetFailureCode := 64;
  if CallResourceReset('legacy-native-complete', False, Reason, ResultDir) and
    (ResourceResetInstallerStatus = 'native-complete') then
    if CallResourceReset('legacy-commit', False, Reason, ResultDir) then
      LegacyAssetsCommitted := GetIniString('result', 'migrationCommitted', '', ResultDir + '\result.ini') = 'True';
  Log('LEGACY_OLD_ENTRY_PENDING');
  SuppressibleMsgBox(LegacyCompletionText, mbInformation, MB_OK, IDOK);
end;

procedure CancelLegacyBeforeNative;
var Reason, ResultDir: String;
begin
  if not LegacyNativeStarted then
    if not CallResourceReset('legacy-cancel', False, Reason, ResultDir) then
      Log('LEGACY_CANCEL_REQUIRES_REVIEW');
end;
