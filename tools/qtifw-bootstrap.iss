; Thin official installer wrapper. The QtIFW helper and its boot directory are
; extracted only to this Setup instance's temporary folder and never copied
; into the selected application directory.
#ifndef HelperRoot
  #error HelperRoot is required
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

[Setup]
AppName=星图
AppVersion={#DisplayVersion}
VersionInfoVersion={#NumericVersion}
VersionInfoProductName=星图
SetupIconFile={#HelperRoot}\boot\xingtu.ico
DefaultDirName={localappdata}\Programs\星图
CreateAppDir=no
Uninstallable=no
CreateUninstallRegKey=no
PrivilegesRequired=admin
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyMemo=no
ShowLanguageDialog=no
UsePreviousLanguage=no
WizardStyle=modern
OutputDir={#SetupOutputDir}
OutputBaseFilename={#SetupName}
Compression=lzma2/ultra64
SolidCompression=yes
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#HelperRoot}\*"; DestDir: "{app}"; Flags: dontcopy noencryption recursesubdirs createallsubdirs

[Code]
var
  TargetDirPage: TInputDirWizardPage;
  SupportReady: Boolean;
  SupportDir: String;
  CheckResultPath: String;
  RunResultPath: String;

function SelectedTarget: String;
begin
  Result := Trim(TargetDirPage.Values[0]);
end;

function ExplicitCommandLineTarget: String;
var
  Index: Integer;
  Argument: String;
begin
  Result := '';
  for Index := 1 to ParamCount do begin
    Argument := ParamStr(Index);
    if CompareText(Copy(Argument, 1, 5), '/DIR=') = 0 then begin
      Result := Copy(Argument, 6, Length(Argument));
      exit;
    end;
    if (CompareText(Argument, '/DIR') = 0) and (Index < ParamCount) then begin
      Result := ParamStr(Index + 1);
      exit;
    end;
  end;
end;

function EnsureSupport(var Reason: String): Boolean;
var
  ExtractedFiles: Integer;
begin
  Result := False;
  Reason := '';
  if not SupportReady then begin
    SupportDir := ExpandConstant('{tmp}\') + '{app}';
    if not ForceDirectories(SupportDir) then begin
      Reason := '无法创建临时安装支持目录。';
      exit;
    end;
    try
      ExtractedFiles := ExtractTemporaryFiles('{app}\*');
    except
      Reason := '无法完整提取安装支持文件。';
      exit;
    end;
    if ExtractedFiles <= 0 then begin
      Reason := '无法提取安装支持文件。';
      exit;
    end;
    if not FileExists(AddBackslash(SupportDir) + 'qtifw-trial-helper.exe') then begin
      Reason := '安装支持文件不完整：缺少 qtifw-trial-helper.exe。';
      exit;
    end;
    if not FileExists(AddBackslash(SupportDir) + 'boot\native-setup.exe') or
       not FileExists(AddBackslash(SupportDir) + 'boot\repository\Updates.xml') or
       not FileExists(AddBackslash(SupportDir) + 'boot\update.rcc') or
       not FileExists(AddBackslash(SupportDir) + 'boot\upgrade-controller.js') or
       not FileExists(AddBackslash(SupportDir) + 'boot\installerbase.exe') then begin
      Reason := '安装支持文件不完整：QtIFW 启动或离线更新文件缺失。';
      exit;
    end;
    SupportReady := True;
  end;
  Result := True;
end;

function ReadDiagnostic(const Filename: String): String;
var
  Lines: TArrayOfString;
  Index: Integer;
begin
  Result := '';
  if LoadStringsFromFile(Filename, Lines) then begin
    for Index := 0 to GetArrayLength(Lines) - 1 do begin
      if Index > 0 then Result := Result + #13#10;
      Result := Result + Lines[Index];
    end;
    Result := Trim(Result);
    if Result <> '' then Log('QtIFW bootstrap diagnostic: ' + Result);
  end;
end;

function FailureMessage(const Filename, Fallback: String): String;
var
  Diagnostic, LowerDiagnostic: String;
begin
  Result := Fallback;
  Diagnostic := ReadDiagnostic(Filename);
  LowerDiagnostic := Lowercase(Diagnostic);
  if Pos('install-directory-not-empty', LowerDiagnostic) > 0 then
    Result := '请选择空目录，或选择原星图 1.1.2 安装目录。'
  else if (Pos('unsupported-upgrade-source', LowerDiagnostic) > 0) or
          (Pos('unsupported-qtifw-installation', LowerDiagnostic) > 0) then
    Result := '此目录中的安装类型不受支持，现有文件未被修改。'
  else if (Pos('program-byte-mismatch', LowerDiagnostic) > 0) or
          (Pos('unowned-upgrade-program-collision', LowerDiagnostic) > 0) then
    Result := '程序文件已被修改或安装目录存在冲突。现场已保留，请核对后重试。'
  else if (Pos('resource', LowerDiagnostic) > 0) and
          ((Pos('busy', LowerDiagnostic) > 0) or (Pos('lease', LowerDiagnostic) > 0) or
           (Pos('lock', LowerDiagnostic) > 0)) then
    Result := '星图或其资源维护任务仍在运行。请正常退出星图后重试。'
  else if (Pos('native-update-failed', LowerDiagnostic) > 0) or
          (Pos('native-install-failed', LowerDiagnostic) > 0) or
          (Pos('persisted-controller-verification-failed', LowerDiagnostic) > 0) then
    Result := '安装或更新未完成。请保留安装目录，并使用同一 Setup 重试。';
  if Length(Result) > 1200 then Result := Copy(Result, 1, 1200);
end;

function InvokeHelper(const Action, ResultPath: String; var ExitCode: Integer;
  var Reason: String): Boolean;
var
  HelperExe, Parameters: String;
begin
  Result := False;
  if not EnsureSupport(Reason) then exit;
  if FileExists(ResultPath) and not DeleteFile(ResultPath) then begin
    Reason := '无法准备本次安装结果记录。';
    exit;
  end;
  HelperExe := AddBackslash(SupportDir) + 'qtifw-trial-helper.exe';
  Parameters := Action + ' ' + AddQuotes(SelectedTarget()) +
    ' --result-file ' + AddQuotes(ResultPath);
  Log('QtIFW bootstrap action=' + Action + '; target=' + SelectedTarget() +
    '; result=' + ResultPath + '; parameters=' + Parameters);
  if not Exec(HelperExe, Parameters, SupportDir, SW_HIDE,
      ewWaitUntilTerminated, ExitCode) then begin
    Reason := '无法启动星图安装支持程序。';
    exit;
  end;
  Result := True;
end;

function BootstrapCheck(var Reason: String): Boolean;
var
  ExitCode: Integer;
begin
  Result := False;
  if not InvokeHelper('bootstrap-check', CheckResultPath, ExitCode, Reason) then exit;
  if (ExitCode = 0) or (ExitCode = 10) then begin
    Result := True;
    Reason := '';
  end else
    Reason := FailureMessage(CheckResultPath,
      '安装检查未通过（错误码 ' + IntToStr(ExitCode) + '）。');
end;

procedure InitializeWizard;
var
  ExplicitTarget: String;
  Index: Integer;
begin
  TargetDirPage := CreateInputDirPage(wpWelcome,
    '选择安装位置', '请选择星图的安装文件夹',
    '安装会保留星图用户数据。', False, '新建文件夹');
  TargetDirPage.Add('安装目录(&D):');
  TargetDirPage.Values[0] := ExpandConstant('{localappdata}\Programs\星图');
  CheckResultPath := ExpandConstant('{tmp}\xingtu-bootstrap-check.json');
  RunResultPath := ExpandConstant('{tmp}\xingtu-bootstrap-result.json');
  { Standard Inno /DIR= and /DIR "path" override only when explicitly passed;
    no path is read from another installer's registry entry. }
  ExplicitTarget := ExplicitCommandLineTarget();
  if ExplicitTarget <> '' then TargetDirPage.Values[0] := ExplicitTarget;
  Log('QtIFW bootstrap selected target: ' + TargetDirPage.Values[0]);
  for Index := 1 to ParamCount do
    Log('QtIFW bootstrap command parameter ' + IntToStr(Index) + ': ' + ParamStr(Index));
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := '安装位置:' + NewLine + Space + SelectedTarget() + NewLine + NewLine +
    '星图用户数据会保留。若安装未完成，将保留现场供诊断。';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Reason: String;
begin
  Result := '';
  if not BootstrapCheck(Reason) then begin
    Result := '安装检查未通过。用户数据和现有文件均已保留。' + #13#10 +
      Reason;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  Reason: String;
begin
  if CurStep = ssInstall then begin
    if not InvokeHelper('bootstrap-run', RunResultPath, ExitCode, Reason) then begin
      SuppressibleMsgBox('安装未完成。请保留安装目录，并使用同一 Setup 重试。' + #13#10 + Reason,
        mbError, MB_OK, IDOK);
      Abort;
    end;
    if ExitCode <> 0 then begin
      Reason := FailureMessage(RunResultPath,
        '安装支持程序返回错误码 ' + IntToStr(ExitCode) + '。');
      SuppressibleMsgBox('安装或更新未完成。请保留安装目录，并使用同一 Setup 重试。' + #13#10 + Reason,
        mbError, MB_OK, IDOK);
      Abort;
    end;
  end;
end;
