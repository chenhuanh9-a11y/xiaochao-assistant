"""Immediate, explicit opt-in, separate from deferred general settings saves."""
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot
from game_startup import WATCHER_NAME, MAIN_NAME, enabled, set_enabled, suppress_games


class GameStartupService(QObject):
    changed = Signal()

    def __init__(self, install_root, *, production=False, parent=None):
        super().__init__(parent)
        self.directory = Path(install_root).resolve()
        self.production = bool(production and os.name == "nt")
        self._scope_valid = False
        self._enabled = False
        self._message = "默认关闭。开启后，下次登录 Windows 起生效；系统可能延迟启动监听。"
        if self.production:
            try:
                from game_startup_win32 import Windows
                self._scope_valid = Windows().interactive_user_matches()
                if not self._scope_valid:
                    self._message = "当前管理员账号与桌面登录账号不同，无法修改游戏启动绑定；请使用同一账号启动星图。"
                else:
                    self._enabled = enabled(self.directory)
            except OSError:
                self._message = "无法读取游戏启动绑定状态，请检查当前用户的启动项权限。"

    @Property(bool, notify=changed)
    def available(self):
        return self.production and self._scope_valid and all((self.directory / name).is_file()
                                       for name in (WATCHER_NAME, MAIN_NAME))

    @Property(bool, notify=changed)
    def enabled(self):
        return self._enabled

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Slot(bool, result=bool)
    def setEnabled(self, value):
        if not self.production or not self._scope_valid or (value and not self.available):
            self._message = "此版本尚未包含完整的游戏启动绑定组件。"
            self.changed.emit()
            return False
        try:
            set_enabled(self.directory, value)
            self._enabled = enabled(self.directory)
            if self._enabled != value:
                raise OSError("启动项写入后校验不一致")
            self._message = ("已开启，下次登录 Windows 起生效。游戏启动时仍需同意 UAC；取消或退出后本次游戏不再提示。"
                             if value else "已关闭；运行中的监听将在数秒内停止，不会关闭游戏或星图。")
        except OSError as error:
            self._message = "设置未完成：" + str(error)
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    @Slot(result=bool)
    def suppressCurrentGame(self):
        """Call only after explicit Quit is accepted, before releasing ownership.

        A separate HKCU value avoids racing the watcher's reservation writer.
        """
        if not self.production or not self._scope_valid or not self._enabled:
            return True
        try:
            from game_startup_win32 import Windows
            windows = Windows()
            suppress_games(self.directory, windows.games(), windows.session)
            return True
        except (OSError, ValueError, KeyError):
            # Conservative fallback: disable registration rather than relaunch
            # after an explicit exit. Failure is reported, never block shutdown.
            try:
                set_enabled(self.directory, False)
                self._enabled = False
            except OSError:
                pass
            self._message = "未能记录本次游戏的退出状态，已尝试关闭游戏启动绑定。"
            self.changed.emit()
            return False
