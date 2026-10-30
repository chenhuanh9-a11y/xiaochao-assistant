; Allowlisted Qt installer profiles; build never installs or launches a product.
#ifndef StageDir
  #error StageDir is required
#endif
#ifndef SetupOutputDir
  #error SetupOutputDir is required
#endif
#ifndef SetupName
  #error SetupName is required
#endif
#ifndef DisplayVersion
  #error DisplayVersion is required
#endif
#ifndef NumericVersion
  #error NumericVersion is required
#endif
#ifndef InstallerAppId
  #define InstallerAppId "{{AF19AFD9-3016-44C9-9D20-6F216C5D8C51}"
#endif
#ifndef InstallerAppName
  #define InstallerAppName "Xingtu Qt Test Client"
#endif
#ifndef InstallerFolder
  #define InstallerFolder "XingtuQtClientTest"
#endif
#ifndef InstallerGroup
  #define InstallerGroup "Xingtu Qt Test Client"
#endif

#ifndef InstallerExeName
  #define InstallerExeName "XingtuQtClientTest.exe"
#endif

[Setup]
AppId={#InstallerAppId}
AppName={#InstallerAppName}
AppVersion={#DisplayVersion}
AppVerName={#InstallerAppName} {#DisplayVersion}
VersionInfoVersion={#NumericVersion}
#ifdef ProductionInstaller
DefaultDirName={localappdata}\Programs\{#InstallerFolder}
UsePreviousAppDir=yes
UsePreviousGroup=yes
DisableDirPage=auto
DisableProgramGroupPage=auto
PrivilegesRequired=lowest
UninstallDisplayName={#InstallerAppName} {#DisplayVersion}
UninstallDisplayIcon={app}\{#InstallerExeName}
AllowNoIcons=yes
#else
AppPublisher=Xingtu project (private Qt test)
DefaultDirName={autopf}\{#InstallerFolder}
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayName={#InstallerAppName}
UninstallDisplayIcon={app}\{#InstallerExeName}
PrivilegesRequired=admin
#endif
DefaultGroupName={#InstallerGroup}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#SetupOutputDir}
OutputBaseFilename={#SetupName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes

#ifdef ProductionInstaller
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked
#endif

[Files]
#ifdef ProductionInstaller
  #ifndef ProductionFilesInclude
    #error ProductionFilesInclude is required for the exact production projection
  #endif
  #include ProductionFilesInclude
#else
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[Icons]
Name: "{group}\{#InstallerAppName}"; Filename: "{app}\{#InstallerExeName}"; WorkingDir: "{app}"

#ifdef ProductionInstaller
Name: "{group}\卸载星图（保留个人资料）"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#InstallerAppName}"; Filename: "{app}\{#InstallerExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
#endif

; No [Run], [UninstallDelete], portable archive, or public URL.
; EXE requests UAC itself. Production preserves existing data paths and refuses
; a busy installed EXE; test instances keep their separate runtime work trees.

#ifdef ProductionInstaller
; Original per-user installer upgrade/rollback and resource preservation contract.
; Reused from 1.0.5 / 64710EF6 effective-installer.iss; no [Run] action copied.
[Code]
(* Program ownership is a relative filename list, never a recursive deletion rule.
   Keep the backup outside {tmp}: failed recovery must survive Setup termination. *)
const
  OwnershipName = '_installer-owned-files.txt';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D04394A0-DACE-4FF9-874D-3DCB43EE53D1}_is1';
var
  UpgradeFiles, UpgradeExisting: TStringList;
  UpgradeBackup: String;
  UpgradeStarted, UpgradeCommitted: Boolean;
  ResourcePlanReady, InstallBaseResources, InstallEditorBase: Boolean;
  ResourcePlanRoot: String;
function InstallerFileAttributes(lpFileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function UpgradePathAllowed: Boolean;
var
  Previous: String;
begin
  Result := True;
  if RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', Previous) then
    Result := CompareText(RemoveBackslashUnlessRoot(Previous),
      RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) = 0;
end;
function NoLinkedAncestors(Path: String): Boolean;
begin
  Result := False;
  while Length(Path) >= Length(ExpandConstant('{app}')) do begin
    if (InstallerFileAttributes(Path) <> $FFFFFFFF) and
      ((InstallerFileAttributes(Path) and $400) <> 0) then Exit;
    Path := ExtractFileDir(Path);
    if Path = ExtractFileDir(Path) then Break;
  end;
  Result := True;
end;
procedure DetermineResourcePlanes;
var
  Target: String;
begin
  Target := ExpandConstant('{app}');
  if ResourcePlanReady and (CompareText(ResourcePlanRoot, Target) = 0) then Exit;
  { Cache BEFORE copying any file: never decide independently for each entry. }
  InstallBaseResources := not (
    DirExists(Target + '\_internal\assets') or
    FileExists(Target + '\_internal\assets') or
    DirExists(Target + '\_internal\references') or
    FileExists(Target + '\_internal\references') or
    DirExists(Target + '\_internal\recognition') or
    FileExists(Target + '\_internal\recognition') or
    FileExists(Target + '\_internal\difficult-native-manifest.json'));
  InstallEditorBase := not (
    DirExists(Target + '\assets\custom-maps') or
    FileExists(Target + '\assets\custom-maps') or
    DirExists(Target + '\_internal\assets\custom-maps') or
    FileExists(Target + '\_internal\assets\custom-maps'));
  ResourcePlanRoot := Target;
  ResourcePlanReady := True;
end;
function InstallBaseResourcePlane: Boolean;
begin
  DetermineResourcePlanes;
  Result := InstallBaseResources;
end;
function InstallEditorResourceFamily: Boolean;
begin
  DetermineResourcePlanes;
  Result := InstallEditorBase;
end;
function ShouldInstallOwnedFile(const Relative: String): Boolean;
var
  Path: String;
begin
  Path := Lowercase(Relative);
  Result := True;
  if (Path = '_internal\version.json') or
    (Path = '_internal\installation-profile.json') or
    (Path = '_internal\references\build-fingerprint.txt') then Exit;
  if Pos('_internal\assets\custom-maps\', Path) = 1 then begin
    Result := InstallEditorResourceFamily; Exit;
  end;
  if (Pos('_internal\assets\', Path) = 1) or
    (Pos('_internal\references\', Path) = 1) or
    (Pos('_internal\recognition\', Path) = 1) or
    (Path = '_internal\difficult-native-manifest.json') then
    Result := InstallBaseResourcePlane;
end;
function SafeProgramPath(const Relative: String): Boolean;
var
  Path, Part: String;
  P: Integer;
begin
  Result := False;
  if (Relative = '') or (Pos('/', Relative) > 0) or
    (Pos(':', Relative) > 0) or (Relative[1] = '\') then Exit;
  Path := Relative;
  while Path <> '' do begin
    P := Pos('\', Path);
    if P = 0 then begin Part := Path; Path := ''; end
    else begin Part := Copy(Path, 1, P - 1); Delete(Path, 1, P); end;
    if (Part = '') or (Part = '.') or (Part = '..') or
      (Part[Length(Part)] = ' ') or (Part[Length(Part)] = '.') then Exit;
  end;
  if (CompareText(Relative, 'work') = 0) or
    (CompareText(Copy(Relative, 1, 5), 'work\') = 0) then Exit;
  { Do not follow a directory junction/symlink outside the selected installation. }
  Path := ExpandConstant('{app}') + '\' + Relative;
  if not NoLinkedAncestors(Path) then Exit;
  Result := True;
end;
procedure AddUpgradePath(const Relative: String);
begin
  if not SafeProgramPath(Relative) then
    RaiseException('安装文件清单含无效或个人资料路径：' + Relative);
  if UpgradeFiles.IndexOf(Relative) < 0 then UpgradeFiles.Add(Relative);
end;
function RetirableProgramFile(const Relative: String): Boolean;
var
  Ext: String;
begin
  { Resource/data files are deliberately never retired by this mechanism. }
  Result := False;
  if (Pos('_internal\assets\', Lowercase(Relative)) = 1) or
    (Pos('_internal\references\', Lowercase(Relative)) = 1) then Exit;
  Ext := Lowercase(ExtractFileExt(Relative));
  Result := (Ext = '.exe') or (Ext = '.dll') or (Ext = '.pyd') or
    (Ext = '.py') or (Ext = '.pyw');
end;
procedure BeginUpgrade;
var
  NewFiles, OldFiles: TStringList;
  I: Integer;
  Relative, Source, Destination: String;
begin
  NewFiles := TStringList.Create;
  OldFiles := TStringList.Create;
  UpgradeFiles := TStringList.Create;
  UpgradeExisting := TStringList.Create;
  try
    ExtractTemporaryFile(OwnershipName);
    NewFiles.LoadFromFile(ExpandConstant('{tmp}\') + OwnershipName);
    DetermineResourcePlanes;
    for I := 0 to NewFiles.Count - 1 do
      if ShouldInstallOwnedFile(NewFiles[I]) then AddUpgradePath(NewFiles[I]);
    if FileExists(ExpandConstant('{app}\') + OwnershipName) then begin
      OldFiles.LoadFromFile(ExpandConstant('{app}\') + OwnershipName);
      for I := 0 to OldFiles.Count - 1 do begin
        if not SafeProgramPath(OldFiles[I]) then
          RaiseException('旧安装文件清单无效；未开始替换。');
        if RetirableProgramFile(OldFiles[I]) then AddUpgradePath(OldFiles[I]);
      end;
    end;
    { Native rollback owns registry/shortcuts. Preserve the prior uninstall log too. }
    AddUpgradePath('unins000.exe');
    AddUpgradePath('unins000.dat');
    AddUpgradePath('unins000.msg');
    UpgradeBackup := ExpandConstant('{app}\work\installer-backups\') +
      GetDateTimeString('yyyymmdd-hhnnss-zzz', '-', ':');
    { The personal-data backup destination must not escape through a junction. }
    if not NoLinkedAncestors(UpgradeBackup) then
      RaiseException('个人资料目录为链接，请先确认安装目录。');
    if DirExists(UpgradeBackup) then RaiseException('安装备份目录已存在，请稍后重试。');
    if not ForceDirectories(UpgradeBackup + '\files') then RaiseException('无法创建安装备份。');
    Source := ExpandConstant('{app}\work\nightmare-memory\ui-settings.json');
    if FileExists(Source) and not FileCopy(Source, UpgradeBackup + '\ui-settings.json', True) then
      RaiseException('无法备份原设置，未开始替换。');
    for I := 0 to UpgradeFiles.Count - 1 do begin
      Relative := UpgradeFiles[I];
      Source := ExpandConstant('{app}\') + Relative;
      if FileExists(Source) then begin
        Destination := UpgradeBackup + '\files\' + IntToStr(UpgradeExisting.Count);
        if not ForceDirectories(ExtractFileDir(Destination)) or
          not FileCopy(Source, Destination, True) then
          RaiseException('无法备份旧文件，未开始替换：' + Relative);
        UpgradeExisting.Add(Relative);
      end;
    end;
    UpgradeFiles.SaveToFile(UpgradeBackup + '\affected.txt');
    UpgradeExisting.SaveToFile(UpgradeBackup + '\existing.txt');
    UpgradeStarted := True;
    Log('UPGRADE_BACKUP_READY ' + UpgradeBackup);
    for I := 0 to OldFiles.Count - 1 do begin
      Relative := OldFiles[I];
      if RetirableProgramFile(Relative) and (NewFiles.IndexOf(Relative) < 0) then begin
        Source := ExpandConstant('{app}\') + Relative;
        if FileExists(Source) and not DeleteFile(Source) then
          RaiseException('旧程序文件无法移除：' + Relative);
        Log('UPGRADE_RETIRED ' + Relative);
      end;
    end;
  finally
    NewFiles.Free;
    OldFiles.Free;
  end;
end;
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then begin
    { Re-evaluate once at the installation boundary, before any actual copy. }
    ResourcePlanReady := False;
    DetermineResourcePlanes;
    if InstallBaseResources then Log('RESOURCE_BASE_INSTALL_COMPLETE_BUNDLE')
    else Log('RESOURCE_BASE_PRESERVE_EXISTING_WHOLE_PLANE');
    if InstallEditorBase then Log('RESOURCE_EDITOR_SEED_COMPLETE_FAMILY')
    else Log('RESOURCE_EDITOR_PRESERVE_EXISTING_FAMILY');
    BeginUpgrade;
  end;
  if CurStep = ssPostInstall then begin
    UpgradeCommitted := True;
    Log('UPGRADE_COMMITTED');
  end;
end;
procedure DeinitializeSetup;
var
  I: Integer;
  Source, Destination: String;
  Restored: Boolean;
begin
  if UpgradeStarted and not UpgradeCommitted then begin
    Restored := True;
    for I := 0 to UpgradeFiles.Count - 1 do begin
      Destination := ExpandConstant('{app}\') + UpgradeFiles[I];
      if UpgradeExisting.IndexOf(UpgradeFiles[I]) >= 0 then begin
        Source := UpgradeBackup + '\files\' + IntToStr(UpgradeExisting.IndexOf(UpgradeFiles[I]));
        if not FileExists(Destination) or
          (GetSHA256OfFile(Source) <> GetSHA256OfFile(Destination)) then begin
          if not ForceDirectories(ExtractFileDir(Destination)) or
            not FileCopy(Source, Destination, False) then Restored := False;
        end;
      end else if FileExists(Destination) then
        if not DeleteFile(Destination) then Restored := False;
    end;
    if Restored then Log('UPGRADE_ROLLBACK_RESTORED')
    else begin
      Log('UPGRADE_ROLLBACK_INCOMPLETE ' + UpgradeBackup);
      SuppressibleMsgBox('安装未完成，部分旧文件无法自动恢复。请保留并交回此备份目录：' +
        UpgradeBackup, mbError, MB_OK, IDOK);
    end;
  end;
  { Backups intentionally remain under work for recovery/audit, including success. }
  if UpgradeFiles <> nil then UpgradeFiles.Free;
  if UpgradeExisting <> nil then UpgradeExisting.Free;
end;
{ Old uninstall logs own bundled maps too. Back them up before allowing uninstall,
  then restore their original paths after native program removal. Never migrate schemas. }
var
  ResourceBackup: String;
  ResourceFiles: TStringList;
  ResourceBackupReady, ResourceRestored, ResourceUninstallStarted: Boolean;
function PreserveResource(const Relative: String): Boolean;
var
  Ext, Path: String;
begin
  Path := Lowercase(Relative);
  Ext := Lowercase(ExtractFileExt(Relative));
  Result := (Pos('_internal\assets\', Path) = 1) or
    (Pos('_internal\references\', Path) = 1) or
    (Ext = '.json') or (Ext = '.jsonl') or (Ext = '.png') or
    (Ext = '.jpg') or (Ext = '.jpeg') or (Ext = '.webp') or
    (Ext = '.bmp') or (Ext = '.npz') or (Ext = '.npy') or (Ext = '.idvm');
end;
function BackupResourceDirectory(const RelativeDirectory: String): Boolean;
var
  FindRec: TFindRec;
  Relative, Source, Destination: String;
begin
  Result := True;
  if FindFirst(ExpandConstant('{app}\') + RelativeDirectory + '\*', FindRec) then begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
          Relative := RelativeDirectory + '\' + FindRec.Name;
          if not SafeProgramPath(Relative) then begin
            Log('UNINSTALL_RESOURCE_PATH_REJECTED ' + Relative);
            Result := False; Exit;
          end;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
            if not BackupResourceDirectory(Relative) then begin Result := False; Exit; end;
          end else if PreserveResource(Relative) then begin
            Source := ExpandConstant('{app}\') + Relative;
            Destination := ResourceBackup + '\files\' + IntToStr(ResourceFiles.Count);
            if not ForceDirectories(ExtractFileDir(Destination)) or
              not FileCopy(Source, Destination, True) then begin
              Log('UNINSTALL_RESOURCE_BACKUP_FAILED ' + Relative);
              Result := False; Exit;
            end;
            ResourceFiles.Add(Relative);
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;
function BackupUninstallResources: Boolean;
begin
  Result := False;
  ResourceFiles := TStringList.Create;
  ResourceBackup := ExpandConstant('{app}\work\installer-backups\uninstall-') +
    GetDateTimeString('yyyymmdd-hhnnss-zzz', '-', ':');
  if not NoLinkedAncestors(ResourceBackup) then Exit;
  if DirExists(ResourceBackup) then Exit;
  if not ForceDirectories(ResourceBackup + '\files') then Exit;
  if not BackupResourceDirectory('_internal') then begin
    SuppressibleMsgBox('无法备份地图资源，卸载已停止；原资料未移除。请检查文件占用和目录权限。', mbError, MB_OK, IDOK);
    Exit;
  end;
  ResourceFiles.SaveToFile(ResourceBackup + '\resources.txt');
  ResourceBackupReady := True;
  Log('UNINSTALL_RESOURCES_BACKED_UP ' + IntToStr(ResourceFiles.Count) + ' ' + ResourceBackup);
  Result := True;
end;
procedure RestoreUninstallResources;
var
  I: Integer;
  Source, Destination: String;
  Success: Boolean;
begin
  if not ResourceBackupReady or not ResourceUninstallStarted or ResourceRestored then Exit;
  Success := True;
  for I := 0 to ResourceFiles.Count - 1 do begin
    Source := ResourceBackup + '\files\' + IntToStr(I);
    Destination := ExpandConstant('{app}\') + ResourceFiles[I];
    if not FileExists(Destination) or
      (GetSHA256OfFile(Source) <> GetSHA256OfFile(Destination)) then begin
      if not ForceDirectories(ExtractFileDir(Destination)) or
        not FileCopy(Source, Destination, False) then Success := False;
    end;
  end;
  ResourceRestored := Success;
  if Success then Log('UNINSTALL_RESOURCES_RESTORED ' + IntToStr(ResourceFiles.Count))
  else begin
    Log('UNINSTALL_RESOURCE_RESTORE_INCOMPLETE ' + ResourceBackup);
    SuppressibleMsgBox('地图原始文件仍保存在以下目录，部分文件无法恢复原位：' +
      ResourceBackup, mbError, MB_OK, IDOK);
  end;
end;
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then ResourceUninstallStarted := True;
  if CurUninstallStep = usPostUninstall then RestoreUninstallResources;
end;
procedure DeinitializeUninstall;
begin
  RestoreUninstallResources;
  if ResourceFiles <> nil then ResourceFiles.Free;
end;
function CreateFileW(lpFileName: String; dwDesiredAccess, dwShareMode,
  lpSecurityAttributes, dwCreationDisposition, dwFlagsAndAttributes,
  hTemplateFile: LongWord): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function InstalledExecutableAvailable: Boolean;
var
  FileName: String;
  Handle: THandle;
begin
  Result := True;
  FileName := ExpandConstant('{app}\第五人格小抄助手.exe');
  if not FileExists(FileName) then Exit;
  { A mapped running executable cannot be opened for exclusive write. Never kill it. }
  Handle := CreateFileW(FileName, $40000000, 0, 0, 3, $80, 0);
  if Handle = THandle(-1) then Result := False
  else CloseHandle(Handle);
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  DetermineResourcePlanes;
  if not UpgradePathAllowed then begin
    Result := '已检测到安装版。覆盖安装必须使用原安装目录，以保留原设置和地图。';
    Exit;
  end;
  if not InstalledExecutableAvailable then
    Result := '星图正在运行，或安装目录不可写。请自行退出助手后重试；安装程序不会结束您的程序。';
end;
function InitializeUninstall: Boolean;
begin
  Result := InstalledExecutableAvailable;
  if not Result then
    SuppressibleMsgBox('星图正在运行，或安装目录不可写。请自行退出助手后再卸载。个人设置、日志与截图将保留。', mbError, MB_OK, IDOK)
  else
    Result := BackupUninstallResources;
end;

#endif
