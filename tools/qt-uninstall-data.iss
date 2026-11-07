{ Production uninstaller data choices. Included inside [Code], after the shared
  lock/path/executable helpers. Native Inno event order and CreateCustomForm:
  https://jrsoftware.org/ishelp/topic_scriptevents.htm
  https://jrsoftware.org/ishelp/topic_isxfunc_createcustomform.htm

  An optional external COPY is a user backup. Temporary same-volume RENAMES
  preserve the one original object across historical native uninstall logs.
  They are not a backup and are removed after a successful keep/clear operation.
  No recursive delete or main-EXE execution. The shared watcher lifecycle owns
  same-user startup registration cleanup; no network or journal deletion. }
var
  UninstallFiles, UninstallHashes, UninstallDirectories: TStringList;
  UninstallHolding, UninstallBackupParent, UninstallBackupPath: String;
  UninstallBackupFinalParent: String;
  UninstallManifestHash, UninstallHashesHash: String;
  UninstallDeleteData, UninstallMakeBackup, UninstallBackupComplete: Boolean;
  UninstallHoldingCreated, UninstallDataFinalized, UninstallDeleteStarted: Boolean;
  UninstallBackupBox: TNewCheckBox;
  UninstallBackupEdit: TNewEdit;
  UninstallBrowseButton: TNewButton;

function UninstallLastError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';
function UninstallGetDriveType(RootPath: String): LongWord;
  external 'GetDriveTypeW@kernel32.dll stdcall';
function UninstallGetFinalPath(Handle: THandle; Buffer: String;
  BufferLength, Flags: LongWord): LongWord;
  external 'GetFinalPathNameByHandleW@kernel32.dll stdcall';

function UninstallHoldingDirectory: String;
begin
  Result := ExpandConstant('{app}\.xingtu-uninstall-retained');
end;

function UninstallSafeAbsolute(Path: String): Boolean;
var
  Parent: String;
  Attributes, Error: LongWord;
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

function UninstallSafeRelative(const Relative: String): Boolean;
var
  Rest, Part: String;
  P: Integer;
begin
  Result := False;
  if (Relative = '') or (Pos('/', Relative) > 0) or (Pos(':', Relative) > 0) or
    (Pos(#13, Relative) > 0) or (Pos(#10, Relative) > 0) or (Relative[1] = '\') then Exit;
  Rest := Relative;
  while Rest <> '' do begin
    P := Pos('\', Rest);
    if P = 0 then begin Part := Rest; Rest := ''; end
    else begin Part := Copy(Rest, 1, P - 1); Delete(Rest, 1, P); end;
    if (Part = '') or (Part = '.') or (Part = '..') or
      (Part[Length(Part)] = ' ') or (Part[Length(Part)] = '.') then Exit;
  end;
  Result := UninstallSafeAbsolute(ExpandConstant('{app}\') + Relative);
end;

function UninstallDataFile(const Relative: String): Boolean;
var
  Path, Ext: String;
begin
  Path := Lowercase(Relative);
  Ext := Lowercase(ExtractFileExt(Relative));
  Result := False;
  { The held cooperative lock is never copied, relocated or deleted. }
  if Path = 'work\map-packages\import.lock' then Exit;
  Result := (Pos('work\', Path) = 1) or
    (Pos('assets\custom-maps\', Path) = 1) or
    (Pos('_internal\assets\', Path) = 1) or
    (Pos('_internal\references\', Path) = 1) or
    ((Pos('_internal\', Path) = 1) and
      ((Ext = '.json') or (Ext = '.jsonl') or (Ext = '.png') or
       (Ext = '.jpg') or (Ext = '.jpeg') or (Ext = '.webp') or
       (Ext = '.bmp') or (Ext = '.npz') or (Ext = '.npy') or (Ext = '.idvm')));
end;

function InspectUninstallDirectory(const Relative: String; Depth: Integer;
  var Entries: Integer): Boolean;
var
  Find: TFindRec;
  Name, Full: String;
  Error: LongWord;
begin
  Result := False;
  if (Depth > 32) or not UninstallSafeRelative(Relative) then Exit;
  Full := ExpandConstant('{app}\') + Relative;
  if not DirExists(Full) then begin
    Result := not FileExists(Full);
    Exit;
  end;
  UninstallDirectories.Add(Relative);
  if not FindFirst(Full + '\*', Find) then begin
    Result := UninstallLastError = 2; { Empty only, not access denied. }
    Exit;
  end;
  try
    repeat
      if (Find.Name <> '.') and (Find.Name <> '..') then begin
        Entries := Entries + 1;
        if (Entries > 50000) or ((Find.Attributes and $400) <> 0) then Exit;
        Name := Relative + '\' + Find.Name;
        if not UninstallSafeRelative(Name) then Exit;
        if (Find.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
          if not InspectUninstallDirectory(Name, Depth + 1, Entries) then Exit;
        end else if UninstallDataFile(Name) then begin
          UninstallFiles.Add(Name);
          UninstallHashes.Add(GetSHA256OfFile(ExpandConstant('{app}\') + Name));
        end;
      end;
      if not FindNext(Find) then begin
        Error := UninstallLastError;
        Result := Error = 18; { ERROR_NO_MORE_FILES only. }
        Break;
      end;
    until False;
  finally
    FindClose(Find);
  end;
end;

function UninstallObjectMatches(const Path, Digest: String): Boolean;
begin
  Result := False;
  try
    Result := UninstallSafeAbsolute(Path) and FileExists(Path) and
      (GetSHA256OfFile(Path) = Digest);
  except
    Result := False;
  end;
end;

function UninstallFinalDirectory(const Path: String): String;
var
  Handle: THandle;
  Buffer: String;
  Count: LongWord;
begin
  Result := '';
  if not DirExists(Path) or not UninstallSafeAbsolute(Path) then Exit;
  { Directory handle + normalized volume-GUID path resolves 8.3 and SUBST aliases.
    No file data access and no fallback to an unverified lexical path. }
  Handle := CreateFileW(Path, 0, 7, 0, 3, $02000000, 0);
  if Handle = THandle(-1) then Exit;
  try
    SetLength(Buffer, 32768);
    Count := UninstallGetFinalPath(Handle, Buffer, 32768, 1);
    if (Count > 0) and (Count < 32768) then
      Result := Lowercase(AddBackslash(Copy(Buffer, 1, Count)));
  finally
    CloseHandle(Handle);
  end;
end;

function UninstallBackupLocationAllowed(const Parent: String): Boolean;
var
  AppPath, Target: String;
  DriveType: LongWord;
begin
  Result := False;
  if Length(Parent) < 3 then Exit;
  if (Parent[2] <> ':') or (Parent[3] <> '\') then Exit;
  DriveType := UninstallGetDriveType(AddBackslash(ExtractFileDrive(Parent)));
  if (DriveType <> 2) and (DriveType <> 3) then Exit; { removable or fixed local }
  AppPath := UninstallFinalDirectory(ExpandConstant('{app}'));
  Target := UninstallFinalDirectory(Parent);
  { The user selects an existing local parent; only a new, unique child is
    created later. Mapped network drives and unresolved aliases fail closed. }
  Result := (AppPath <> '') and (Target <> '') and (Pos(AppPath, Target) <> 1);
end;

procedure UninstallBackupChoiceChanged(Sender: TObject);
begin
  UninstallBackupEdit.Enabled := UninstallBackupBox.Checked;
  UninstallBrowseButton.Enabled := UninstallBackupBox.Checked;
end;

procedure BrowseUninstallBackup(Sender: TObject);
var
  Folder: String;
begin
  Folder := UninstallBackupEdit.Text;
  if BrowseForFolder('选择安装目录之外的备份位置', Folder, True) then
    UninstallBackupEdit.Text := Folder;
end;

function ChooseUninstallData: Boolean;
var
  Form: TSetupForm;
  Text: TNewStaticText;
  DeleteBox: TNewCheckBox;
  NextButton, CancelButton: TNewButton;
  Summary: String;
  Answer: Integer;
begin
  Result := False;
  UninstallDeleteData := False;
  UninstallMakeBackup := False;
  { A silent invocation cannot opt into destructive choices or backup copies. }
  if UninstallSilent then begin Result := True; Exit; end;
  #if Ver >= EncodeVer(6, 6, 0)
  Form := CreateCustomForm(540, 365, True, True);
  #else
  Form := CreateCustomForm;
  Form.ClientWidth := ScaleX(540);
  Form.ClientHeight := ScaleY(365);
  #endif
  try
    Form.Caption := '星图卸载向导 · 个人资料';
    Form.Position := poScreenCenter;
    Text := TNewStaticText.Create(Form);
    Text.Parent := Form;
    Text.SetBounds(ScaleX(20), ScaleY(16), ScaleX(500), ScaleY(64));
    Text.AutoSize := False;
    Text.WordWrap := True;
    Text.Caption := '默认只卸载程序，保留本机资料。不勾选备份不会删除资料。' + #13#10 +
      '资料范围：本安装目录内的设置、图包及资源、自定义路线、日志、截图和历史备份。';
    DeleteBox := TNewCheckBox.Create(Form);
    DeleteBox.Parent := Form;
    DeleteBox.SetBounds(ScaleX(20), ScaleY(87), ScaleX(500), ScaleY(26));
    DeleteBox.Caption := '同时清除上述资料（不经过回收站）';
    DeleteBox.Checked := False;
    UninstallBackupBox := TNewCheckBox.Create(Form);
    UninstallBackupBox.Parent := Form;
    UninstallBackupBox.SetBounds(ScaleX(20), ScaleY(128), ScaleX(500), ScaleY(26));
    UninstallBackupBox.Caption := '卸载前另存一份资料备份（可选）';
    UninstallBackupBox.Checked := False;
    UninstallBackupBox.OnClick := @UninstallBackupChoiceChanged;
    UninstallBackupEdit := TNewEdit.Create(Form);
    UninstallBackupEdit.Parent := Form;
    UninstallBackupEdit.SetBounds(ScaleX(20), ScaleY(163), ScaleX(401), ScaleY(24));
    UninstallBackupEdit.Text := ExpandConstant('{userdocs}');
    UninstallBrowseButton := TNewButton.Create(Form);
    UninstallBrowseButton.Parent := Form;
    UninstallBrowseButton.SetBounds(ScaleX(431), ScaleY(161), ScaleX(89), ScaleY(28));
    UninstallBrowseButton.Caption := '浏览…';
    UninstallBrowseButton.OnClick := @BrowseUninstallBackup;
    UninstallBackupChoiceChanged(nil);
    Text := TNewStaticText.Create(Form);
    Text.Parent := Form;
    Text.SetBounds(ScaleX(20), ScaleY(207), ScaleX(500), ScaleY(91));
    Text.AutoSize := False;
    Text.WordWrap := True;
    Text.Caption := '外部 ZIP、安装目录外的文件及未识别的邻近文件不在清除范围。游戏启动绑定单独停用，外部防重复提示记录保留。' + #13#10 +
      '卸载期间会临时移动原文件，以避开旧安装记录的自动删除；不会因此多复制一份备份。' + #13#10 +
      '下一步可核对选择；取消或关闭此向导不会移动、备份或删除资料。';
    NextButton := TNewButton.Create(Form);
    NextButton.Parent := Form;
    NextButton.SetBounds(ScaleX(328), ScaleY(317), ScaleX(92), ScaleY(30));
    NextButton.Caption := '下一步';
    NextButton.Default := True;
    NextButton.ModalResult := mrOK;
    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.SetBounds(ScaleX(430), ScaleY(317), ScaleX(90), ScaleY(30));
    CancelButton.Caption := '取消';
    CancelButton.Cancel := True;
    CancelButton.ModalResult := mrCancel;
    while Form.ShowModal = mrOK do begin
      UninstallBackupParent := Trim(UninstallBackupEdit.Text);
      if UninstallBackupBox.Checked and not UninstallBackupLocationAllowed(UninstallBackupParent) then
        MsgBox('请选择安装目录之外、已存在且不含链接的本地备份位置。', mbError, MB_OK)
      else begin
        if DeleteBox.Checked then Summary := '已选择清除资料；查看程序卸载结果后还需最后确认。清除不能通过回收站恢复。'
        else Summary := '仅卸载程序，个人资料和地图资源保留在原安装目录。';
        if UninstallBackupBox.Checked then
          Summary := Summary + #13#10 + '另存备份到：' + UninstallBackupParent + '\卸载备份-<时间>'
        else Summary := Summary + #13#10 + '不另存备份。';
        Summary := Summary + #13#10#13#10 + '是：继续；否：返回修改；取消：退出卸载。';
        Answer := MsgBox(Summary, mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON3);
        if Answer = IDYES then begin
          UninstallDeleteData := DeleteBox.Checked;
          UninstallMakeBackup := UninstallBackupBox.Checked;
          if UninstallMakeBackup then
            UninstallBackupFinalParent := UninstallFinalDirectory(UninstallBackupParent);
          Result := True;
          Break;
        end;
        if Answer = IDCANCEL then Break;
      end;
    end;
  finally
    Form.Free;
  end;
end;

function BackupUninstallResources: Boolean;
var
  I: Integer;
  Source, Destination: String;
begin
  Result := False;
  if not UninstallBackupLocationAllowed(UninstallBackupParent) then Exit;
  if UninstallFinalDirectory(UninstallBackupParent) <> UninstallBackupFinalParent then Exit;
  UninstallBackupPath := AddBackslash(UninstallBackupParent) + '卸载备份-' +
    GetDateTimeString('yyyymmdd-hhnnss-zzz', '-', ':');
  if not CreateDir(UninstallBackupPath) then Exit;
  if UninstallFinalDirectory(UninstallBackupPath) <>
    UninstallBackupFinalParent + Lowercase(ExtractFileName(UninstallBackupPath)) + '\' then Exit;
  if not SaveStringToFile(UninstallBackupPath + '\INCOMPLETE.txt',
    '备份状态以文件名为准：INCOMPLETE 表示未完成；COMPLETED 表示清单全部复制并核对成功。', False) then Exit;
  for I := 0 to UninstallFiles.Count - 1 do begin
    Source := ExpandConstant('{app}\') + UninstallFiles[I];
    Destination := UninstallBackupPath + '\files\' + UninstallFiles[I];
    if not UninstallObjectMatches(Source, UninstallHashes[I]) or
      not UninstallSafeAbsolute(Destination) or
      not ForceDirectories(ExtractFileDir(Destination)) or
      not FileCopy(Source, Destination, True) or
      not UninstallObjectMatches(Source, UninstallHashes[I]) or
      not UninstallObjectMatches(Destination, UninstallHashes[I]) then Exit;
  end;
  UninstallFiles.SaveToFile(UninstallBackupPath + '\paths.txt');
  UninstallHashes.SaveToFile(UninstallBackupPath + '\sha256.txt');
  if not RenameFile(UninstallBackupPath + '\INCOMPLETE.txt',
    UninstallBackupPath + '\COMPLETED.txt') then Exit;
  UninstallBackupComplete := True;
  Result := True;
end;

function OptionalUninstallBackup: Boolean;
var
  Success: Boolean;
  Reason: String;
  Answer: Integer;
begin
  Result := True;
  if not UninstallMakeBackup then Exit;
  repeat
    Reason := '无法完整复制或核对资料，请检查可用空间、目录权限和文件占用。';
    try
      Success := BackupUninstallResources;
    except
      Success := False;
      Reason := GetExceptionMessage;
    end;
    if Success then Exit;
    Answer := SuppressibleMsgBox('备份未完成：' + Reason + #13#10 +
      '原资料尚未移动或删除。不完整备份（如已创建）保留在：' + UninstallBackupPath + #13#10#13#10 +
      '是：重试；否：不备份继续；取消：停止卸载。',
      mbError, MB_YESNOCANCEL or MB_DEFBUTTON3, IDCANCEL);
    if Answer = IDNO then begin
      if UninstallDeleteData then
        if SuppressibleMsgBox('将不另存备份，并在卸载后清除已选择的资料，无法通过回收站恢复。仍要继续？',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) <> IDYES then begin
          Result := False; Exit;
        end;
      UninstallMakeBackup := False;
      Exit;
    end;
    if Answer <> IDYES then begin Result := False; Exit; end;
  until False;
end;

function CleanupUninstallHolding: Boolean;
begin
  Result := False;
  if not UninstallHoldingCreated then begin Result := True; Exit; end;
  { Never recursively delete a holding directory or an object we did not stage. }
  if not UninstallSafeAbsolute(UninstallHolding) then Exit;
  if DirExists(UninstallHolding + '\files') then
    if not RemoveDir(UninstallHolding + '\files') then Exit;
  { Cleanup may have partially succeeded before an exception. Missing audit
    files are acceptable only after every original was restored or cleared. }
  if FileExists(UninstallHolding + '\paths.txt') then begin
    if not UninstallObjectMatches(UninstallHolding + '\paths.txt', UninstallManifestHash) then Exit;
    if not DeleteFile(UninstallHolding + '\paths.txt') then Exit;
  end;
  if FileExists(UninstallHolding + '\sha256.txt') then begin
    if not UninstallObjectMatches(UninstallHolding + '\sha256.txt', UninstallHashesHash) then Exit;
    if not DeleteFile(UninstallHolding + '\sha256.txt') then Exit;
  end;
  Result := RemoveDir(UninstallHolding);
end;

procedure RestoreUninstallResources;
var
  I: Integer;
  Source, Destination: String;
  Success: Boolean;
begin
  if not UninstallHoldingCreated or UninstallDataFinalized or UninstallDeleteStarted then Exit;
  Success := True;
  for I := 0 to UninstallFiles.Count - 1 do begin
    Source := UninstallHolding + '\files\' + IntToStr(I);
    Destination := ExpandConstant('{app}\') + UninstallFiles[I];
    if FileExists(Source) then begin
      { No overwrite, even if a new destination happens to have equal bytes. }
      if not UninstallObjectMatches(Source, UninstallHashes[I]) or
        not UninstallSafeRelative(UninstallFiles[I]) or FileExists(Destination) or DirExists(Destination) or
        not ForceDirectories(ExtractFileDir(Destination)) or
        not RenameFile(Source, Destination) then Success := False;
    end else if not UninstallObjectMatches(Destination, UninstallHashes[I]) then Success := False;
  end;
  if Success then Success := CleanupUninstallHolding;
  UninstallDataFinalized := Success;
  if Success then Log('UNINSTALL_DATA_KEPT_WITHOUT_BACKUP_COPY')
  else begin
    Log('UNINSTALL_DATA_RESTORE_INCOMPLETE ' + UninstallHolding);
    SuppressibleMsgBox('部分资料未能放回原位，未覆盖后来出现的文件。请保留以下目录及其中的 paths.txt、sha256.txt，核对后恢复：' +
      UninstallHolding + #13#10 + '本次卸载的数据保留步骤未完成。', mbError, MB_OK, IDOK);
  end;
end;

function PrepareUninstallData: Boolean;
var
  I, Entries: Integer;
  Source, Destination: String;
begin
  Result := False;
  UninstallFiles := TStringList.Create;
  UninstallHashes := TStringList.Create;
  UninstallDirectories := TStringList.Create;
  Entries := 0;
  if not InspectUninstallDirectory('_internal', 0, Entries) or
    not InspectUninstallDirectory('work', 0, Entries) or
    not InspectUninstallDirectory('assets\custom-maps', 0, Entries) then
    RaiseException('无法安全检查资料，尚未开始卸载。请检查目录链接、权限或文件数量。');
  if not OptionalUninstallBackup then Exit;
  UninstallHolding := UninstallHoldingDirectory;
  if not UninstallSafeAbsolute(UninstallHolding) or not CreateDir(UninstallHolding) then
    RaiseException('无法创建临时保留目录，尚未开始卸载。若有上次中断资料，请先核对：' + UninstallHolding);
  UninstallHoldingCreated := True;
  { Write the complete path/hash recovery mapping BEFORE the first rename. }
  UninstallFiles.SaveToFile(UninstallHolding + '\paths.txt');
  UninstallHashes.SaveToFile(UninstallHolding + '\sha256.txt');
  UninstallManifestHash := GetSHA256OfFile(UninstallHolding + '\paths.txt');
  UninstallHashesHash := GetSHA256OfFile(UninstallHolding + '\sha256.txt');
  if not CreateDir(UninstallHolding + '\files') then
    RaiseException('无法建立临时保留目录，尚未开始卸载；原资料未移动。');
  for I := 0 to UninstallFiles.Count - 1 do begin
    Source := ExpandConstant('{app}\') + UninstallFiles[I];
    Destination := UninstallHolding + '\files\' + IntToStr(I);
    if not UninstallObjectMatches(Source, UninstallHashes[I]) or
      not UninstallSafeAbsolute(Destination) or not RenameFile(Source, Destination) then
      RaiseException('无法安全保留原资料，卸载已停止；正在还原已移动的文件。');
  end;
  Log('UNINSTALL_DATA_RETAINED_BY_RENAME ' + IntToStr(UninstallFiles.Count));
  Result := True;
end;

procedure ClearSelectedUninstallData;
var
  I: Integer;
  Path: String;
  Success: Boolean;
begin
  { Destructive commit exists only after native result presentation and a new
    explicit clear confirmation. Inno's post/done events do NOT prove success. }
  if not UninstallDeleteData or not UninstallHoldingCreated then Exit;
  for I := 0 to UninstallFiles.Count - 1 do
    if not UninstallObjectMatches(UninstallHolding + '\files\' + IntToStr(I), UninstallHashes[I]) then begin
      RestoreUninstallResources;
      RaiseException('资料在卸载期间发生变化，未执行清除；已尝试保留原资料。');
    end;
  UninstallDeleteStarted := True;
  Success := True;
  for I := 0 to UninstallFiles.Count - 1 do begin
    Path := UninstallHolding + '\files\' + IntToStr(I);
    if not UninstallObjectMatches(Path, UninstallHashes[I]) or not DeleteFile(Path) then begin
      Success := False;
      Log('UNINSTALL_DATA_CLEAR_RETAINED ' + UninstallFiles[I]);
    end;
  end;
  if Success then Success := CleanupUninstallHolding;
  if Success then begin
    { Empty directories only; never consume new or unrecognised neighbours. }
    for I := UninstallDirectories.Count - 1 downto 0 do
      if UninstallSafeRelative(UninstallDirectories[I]) then
        RemoveDir(ExpandConstant('{app}\') + UninstallDirectories[I]);
    UninstallDataFinalized := True;
    Log('UNINSTALL_SELECTED_DATA_CLEARED');
  end else begin
    Log('UNINSTALL_DATA_CLEAR_INCOMPLETE ' + UninstallHolding);
    SuppressibleMsgBox('资料没有全部清除。已清除的部分不能自动撤回；其余文件和路径清单保留在：' +
      UninstallHolding + #13#10 + '未选择或后来新增的文件不会被递归删除。', mbError, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  WatcherReason: String;
begin
  if CurUninstallStep = usUninstall then begin
    { Abort here is an Inno-supported pre-uninstall exit. No native removals yet. }
    if not InstalledExecutableAvailable or not AcquireResourceLock then
      RaiseException('星图或资源任务正在运行，尚未开始卸载。请退出后重试。');
    { A different installer may have left recovery state while this wizard
      waited for the user's choice. Only this locked recheck is authoritative. }
    if FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
      FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then
      RaiseException('资源升级尚未恢复，不能卸载或移动其恢复证据。请先核对程序与资源。');
    if not DisableGameWatcher(WatcherReason) then RaiseException(WatcherReason);
    if not PrepareUninstallData then Abort;
    { All cancellable preparation succeeded. Native removal starts after this
      event returns; never re-enable Run against removed/partial program files. }
    CommitGameWatcherUninstall;
  end;
  if CurUninstallStep = usPostUninstall then begin
    if not UninstallDeleteData then RestoreUninstallResources;
  end;
  if CurUninstallStep = usDone then begin
    { Inno invokes usPostUninstall AND usDone even when RemovedAll=False.
      usDone follows its native result/restart dialog. Never infer success. }
    if UninstallDeleteData then begin
      if SuppressibleMsgBox('请结合刚才的 Windows 卸载结果：程序可能仍有未移除的文件。' + #13#10 +
        '仍要永久清除此前选择的设置、图包、路线、日志、截图和历史备份吗？' + #13#10 +
        '是：清除资料；否：保留原资料。清除不经过回收站。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        ClearSelectedUninstallData
      else begin
        UninstallDeleteData := False;
        RestoreUninstallResources;
      end;
    end;
    if UninstallBackupComplete then
      SuppressibleMsgBox('另存的资料备份已完整核对，保存在：' + UninstallBackupPath + #13#10 +
        '备份不会随本次卸载清除。', mbInformation, MB_OK, IDOK);
  end;
end;

procedure DeinitializeUninstall;
var
  WatcherReason: String;
begin
  try
    { Also restores partial staging after cancellation or a preparation error. }
    RestoreUninstallResources;
  finally
    { Pending only when native removal never started. Backup cancellation and
      preparation failure must not silently discard the user's previous opt-in. }
    if not RestoreGameWatcher(WatcherReason) then
      SuppressibleMsgBox(WatcherReason, mbError, MB_OK, IDOK);
    if UninstallFiles <> nil then UninstallFiles.Free;
    if UninstallHashes <> nil then UninstallHashes.Free;
    if UninstallDirectories <> nil then UninstallDirectories.Free;
    ReleaseResourceLock;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := False;
  if FileExists(ExpandConstant('{app}\work\pending-installer-upgrade.json')) or
      FileExists(ExpandConstant('{app}\work\map-packages\pending-resource-reset.json')) then begin
    SuppressibleMsgBox('上次资源升级尚未完成，请先核对恢复程序与资源；卸载不会覆盖或清除待恢复资料。',
      mbError, MB_OK, IDOK);
    Exit;
  end;
  if DirExists(UninstallHoldingDirectory) or FileExists(UninstallHoldingDirectory) then begin
    SuppressibleMsgBox('检测到上次中断的卸载保留目录，请先按路径清单核对恢复，不能覆盖或忽略：' +
      UninstallHoldingDirectory, mbError, MB_OK, IDOK);
    Exit;
  end;
  if not InstalledExecutableAvailable then begin
    SuppressibleMsgBox('星图正在运行，或安装目录不可写。请退出后再卸载；资料尚未改变。', mbError, MB_OK, IDOK);
    Exit;
  end;
  Result := ChooseUninstallData;
end;
