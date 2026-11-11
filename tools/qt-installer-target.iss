{ Task 2 owns old QtIFW migration; never launch its maintenance uninstaller here. }
function InstallerNormalizedDirectory(Path: String): String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandFileName(Path));
end;

function InstallerDirectoriesOverlap(const A, B: String): Boolean;
begin
  Result := (Pos(Lowercase(AddBackslash(A)), Lowercase(AddBackslash(B))) = 1) or
    (Pos(Lowercase(AddBackslash(B)), Lowercase(AddBackslash(A))) = 1);
end;

function InstallerTargetLastError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';

function InstallerTargetError: String;
var Target, Path, Parent, Previous, Key, CurrentId: String; Ids: TStringList;
  I, View, Entries: Integer; Root: Integer; Attributes, Error: LongWord; Find: TFindRec;
  RegisteredHere: Boolean;
begin
  Result := '';
  Target := InstallerNormalizedDirectory(ExpandConstant('{app}'));
  if (Length(Target) <= 3) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{autopf}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{win}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{sys}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{userdesktop}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{localappdata}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{userappdata}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{commonappdata}'))) = 0) or
    (CompareText(Target, InstallerNormalizedDirectory(ExpandConstant('{userdocs}'))) = 0) or
    (Pos(Lowercase(AddBackslash(Target)), Lowercase(AddBackslash(InstallerNormalizedDirectory(GetEnv('USERPROFILE'))))) = 1) then begin
    Result := '请选择本次星图独立使用的安装子目录，不能使用系统或个人资料根目录。'; Exit;
  end;
  Path := Target;
  repeat
    Attributes := InstallerFileAttributes(Path);
    if Attributes = $FFFFFFFF then begin
      Error := InstallerTargetLastError;
      if (Error <> 2) and (Error <> 3) then begin
        Result := '无法读取安装路径，尚未修改程序或资料。'; Exit;
      end;
    end else if (Attributes and $400) <> 0 then begin
      Result := '安装路径含链接或联接点，请选择独立的普通目录。'; Exit;
    end;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Break;
    Path := Parent;
  until Path = '';
  Ids := TStringList.Create;
  RegisteredHere := False;
  try
    Ids.Add('{D04394A0-DACE-4FF9-874D-3DCB43EE53D1}');
    Ids.Add('{AF19AFD9-3016-44C9-9D20-6F216C5D8C51}');
    Ids.Add('{D4B8E562-EA62-4A43-AF1D-043A5646971E}');
    Ids.Add('{907F912D-58FD-4F84-A7A4-0C816AA70E52}');
    Ids.Add('{3C271F5D-71FF-4EA8-9569-B8DAC30F3773}');
    Ids.Add('{ED53B6E8-E9A0-4F21-AF75-7C8A62BE7347}');
    Ids.Add('{D9FE6D76-C31A-47AF-9281-B4614C245E92}');
    Ids.Add('{7A2F6C81-9B54-4DB6-A308-6E452D91F0BC}');
    CurrentId := ExpandConstant('{#InstallerAppId}');
    for I := 0 to Ids.Count - 1 do
      for View := 0 to 3 do begin
        case View of
          0: Root := HKCU64;
          1: Root := HKLM64;
          2: Root := HKCU32;
          3: Root := HKLM32;
        end;
        Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Ids[I] + '_is1';
        if RegQueryStringValue(Root, Key, 'InstallLocation', Previous) and (Previous <> '') then begin
          Previous := InstallerNormalizedDirectory(Previous);
          if CompareText(Ids[I], CurrentId) = 0 then begin
            if CompareText(Previous, Target) = 0 then RegisteredHere := True
            else begin Result := '本安装渠道已有登记目录，请从原目录升级；迁移入口尚未接通。'; Exit; end;
          end else if InstallerDirectoriesOverlap(Previous, Target) then begin
            Result := '安装位置与另一正式版或测试实例重叠，请选择独立目录。'; Exit;
          end;
        end;
      end;
  finally
    Ids.Free;
  end;
  if FileExists(Target + '\MaintenanceTool.exe') or FileExists(Target + '\components.xml') or
    FileExists(Target + '\installer.dat') or FileExists(Target + '\maintenancetool.dat') then begin
    Result := '检测到旧版维护工具。资料迁移入口尚未接通，本次不会覆盖原目录，也不会先调用卸载。'; Exit;
  end;
  if DirExists(Target) and not RegisteredHere then begin
    Entries := 0;
    if FindFirst(Target + '\*', Find) then begin
      try
        repeat
          if (Find.Name <> '.') and (Find.Name <> '..') then Entries := Entries + 1;
          if not FindNext(Find) then begin
            if InstallerTargetLastError <> 18 then begin
              Result := '无法完整读取安装目录，尚未修改程序或资料。'; Exit;
            end;
            Break;
          end;
        until False;
      finally
        FindClose(Find);
      end;
    end else if InstallerTargetLastError <> 2 then begin
      Result := '无法读取安装目录，尚未修改程序或资料。'; Exit;
    end;
    if Entries > 0 then
      Result := '非空目录没有本安装渠道的登记，不能覆盖或认领其中资料。请选择空目录或先完成旧版迁移。';
  end;
end;
