// Include inside [Code]. No Run entry is created for a fresh/off installation.
// Installer event owner calls Capture -> Disable before mutation, Restore on
// upgrade success or abort before uninstall; never Restore after uninstall begins.
var
  GameWatcherCaptured: Boolean;
  GameWatcherWasEnabled: Boolean;
  GameWatcherRestorePending: Boolean;
  GameWatcherOriginalDir: String;

function RunGameWatcherControl(const Directory, Command: String; var Code: Integer): Boolean;
begin
  Code := -1;
  Result := Exec(AddBackslash(Directory) + 'XingtuGameWatcher.exe', Command,
    Directory, SW_HIDE, ewWaitUntilTerminated, Code);
end;

function CaptureGameWatcher(var Reason: String): Boolean;
var Code: Integer;
begin
  Result := True;
  Reason := '';
#ifdef ProductionInstaller
  if GameWatcherCaptured then begin
    if CompareText(GameWatcherOriginalDir, ExpandConstant('{app}')) <> 0 then begin
      Reason := '安装目录在游戏绑定检查后发生变化。请退出本次安装，恢复原绑定后重新运行安装程序。';
      Result := False;
    end;
    exit;
  end;
  GameWatcherOriginalDir := ExpandConstant('{app}');
  if FileExists(AddBackslash(GameWatcherOriginalDir) + 'XingtuGameWatcher.exe') then begin
    if not RunGameWatcherControl(GameWatcherOriginalDir, '--status', Code) then begin
      Reason := '无法读取游戏启动绑定状态，尚未修改安装或用户资料。'; Result := False; exit;
    end;
    if (Code <> 0) and (Code <> 10) then begin
      Reason := '无法确认当前桌面账号的游戏启动绑定状态（错误码 ' + IntToStr(Code) + '）。请使用相同账号并关闭其他 Windows 会话后重试。';
      Result := False; exit;
    end;
    GameWatcherWasEnabled := Code = 10;
  end else GameWatcherWasEnabled := False;
  GameWatcherCaptured := True;
#endif
end;

function DisableGameWatcher(var Reason: String): Boolean;
var Code: Integer;
begin
  Result := CaptureGameWatcher(Reason);
  if not Result then exit;
#ifdef ProductionInstaller
  if not FileExists(AddBackslash(GameWatcherOriginalDir) + 'XingtuGameWatcher.exe') then exit;
  { Even timeout may already have removed Run. Keep abort restoration owed. }
  if GameWatcherWasEnabled then GameWatcherRestorePending := True;
  if not RunGameWatcherControl(GameWatcherOriginalDir, '--disable', Code) or (Code <> 0) then begin
    Reason := '未能确认游戏启动监听已退出（错误码 ' + IntToStr(Code) + '）。请取消待处理的管理员提示、关闭其他登录会话后重试；没有强制结束游戏或程序。';
    Result := False;
  end;
#endif
end;

function RestoreGameWatcher(var Reason: String): Boolean;
var Code: Integer;
begin
  Result := True;
  Reason := '';
#ifdef ProductionInstaller
  if not GameWatcherRestorePending then exit;
  if not RunGameWatcherControl(GameWatcherOriginalDir, '--restore-after-upgrade', Code) or (Code <> 0) then begin
    Reason := '原先开启的游戏启动绑定未能恢复（错误码 ' + IntToStr(Code) + '）。安装资料保留；请在星图设置中核对并重新开启。';
    Result := False; exit;
  end;
  GameWatcherRestorePending := False;
#endif
end;

procedure CommitGameWatcherUninstall;
begin
  { Call only when native uninstall is about to start; cancellation before that
    point must RestoreGameWatcher while the original complete helper exists. }
  GameWatcherRestorePending := False;
end;
