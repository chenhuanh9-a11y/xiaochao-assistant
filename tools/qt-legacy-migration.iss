{ Pre-native preparation only. Never run an old maintenance/uninstall tool.
  Keep this before InstallerTargetError and before the target resource lease.
  The existing Inno preserve coordinator continues to own registered upgrades. }
var
  LegacySourcePage: TInputDirWizardPage;
  LegacyPreparationCall: Integer;

procedure InitializeWizard;
begin
  LegacySourcePage := CreateInputDirPage(wpSelectDir, '旧版资料迁移',
    '从旧版升级时，请选择原安装目录；全新安装可留空。',
    '旧版资料先复制并逐项校验。新安装位置必须是另一个空目录。' + #13#10 +
    '此草稿尚未完成旧版退出与 Windows 安装联合验收，准备好副本后会停止，不会卸载旧版或切换程序。',
    False, '');
  LegacySourcePage.Add('原安装目录（全新安装留空）：');
end;

function PrepareLegacyMigration: String;
var Source, Target, ResultDir, Params, Receipt, Status: String; Code: Integer;
begin
  Result := '';
  Source := Trim(LegacySourcePage.Values[0]);
  if Source = '' then Exit;
  Target := ExpandConstant('{app}');
  if CompareText(InstallerNormalizedDirectory(Source), InstallerNormalizedDirectory(Target)) = 0 then begin
    Result := '旧版迁移准备需要另一个空目录；不会在原目录写入新程序或运行旧卸载器。'; Exit;
  end;
  try
    ExtractAndVerifyResetHelper;
    LegacyPreparationCall := LegacyPreparationCall + 1;
    ResultDir := ExpandConstant('{tmp}\legacy-preparation-') + IntToStr(LegacyPreparationCall);
    if not ResetSafePath(ResultDir) or not CreateDir(ResultDir) then
      RaiseException('无法创建独立的资料迁移检查目录。');
    Params := 'legacy-prepare --source ' + AddQuotes(Source) + ' --target ' + AddQuotes(Target) +
      ' --result-directory ' + AddQuotes(ResultDir);
    Code := -1;
    if not Exec(ResourceResetExtractRoot + '\star-map-reset-helper.exe', Params,
      ResourceResetExtractRoot, SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('资料迁移检查程序未完成；不会继续程序安装。');
    Receipt := ResultDir + '\result.ini';
    Status := GetIniString('result', 'status', '', Receipt);
    if (Code <> 0) or (Status <> 'prepared-native-unverified') then begin
      Result := '旧版资料尚未完整准备。请核对产品登记、目录占用及外置地图资源；原资料保留，本次不会继续安装。';
      Exit;
    end;
    Result := '旧版资料副本已复制并校验，原资料保留。旧版退出和 Windows 安装的联合验收尚未完成，' +
      '本次已在切换程序前停止；请保留新目录中的迁移副本与恢复记录。此结果不代表升级成功。';
  except
    Result := GetExceptionMessage;
  end;
end;
