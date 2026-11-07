# 游戏启动绑定：实现与集成合同

默认关闭。用户在设置页明确开启后，注册当前用户 HKCU Run；**下次登录 Windows 起生效**。
Windows 可能延迟 Run 项，不承诺立即启动，也不声称启用后当前登录立刻开始监听。
主程序继续 requireAdministrator；监听器单独 asInvoker，拒绝以管理员运行。
监听器每 3 秒只枚举窗口/有限进程元数据，不加载 Qt、OpenCV、图包或识别引擎。

## 身份与生命周期

- 同一 Windows 会话、可见（包括最小化）窗口、完整限定标题规则与 dwrg.exe 同时匹配。
  标题规则与现有 identityv_live_mvp 保持一致，但独立轻量实现避免加载采集依赖。
- PID + GetProcessTimes 创建时间标记一次游戏进程。启动时游戏已在运行也会检测。
- 用户态 Local mutex 防同一安装/用户/会话的多个 watcher。启动前原子保存尝试记录，
  UAC 取消、启动失败、主程序已运行、身份不可读均不重复弹窗。游戏重启为新身份。
- 主程序存在性以同会话、准确安装路径为准；查询失败为 unknown，保守消费本轮而不启动。
- 用户明确退出主程序时写按安装与 Windows 会话隔离的独立 HKCU exit 标记（不以管理员权限写可重定向的用户目录）；不要对隐藏/后台/取消调用。主程序不存在检查
  在读 exit 标记 **之前**，退出写入在释放主实例 **之前**，避免首次轮询的重启竞态。
- 普通窗口暂时消失不清记录。最多 256 个身份有界保存，LocalAppData 独立于管理员安装目录。满额时仅移除原生查询已证实消失/创建时间改变的身份；存活或查询未知的身份不驱逐，无法腾出空间则停止而非重复提示。
  损坏记录或写失败停止监听，不清空后再次请求 UAC。程序不记录窗口内容或截图。
- 关闭开关删除 Run 项，运行中的 watcher 下一轮退出。已经提交给 Windows 的 UAC 对话框
  不能撤回；用户仍可取消。关闭不结束游戏/主程序。无计划任务、服务、令牌绕过。

## 必须完成的集成

1. 主入口创建并保留 `GameStartupService(INSTALL_ROOT, production=PRODUCTION)`；
   QML context 注册 `gameStartupService`。ClientSettings.qml 已有守卫后的即时开关。
2. 仅在主程序明确退出已接受时调用 `suppressCurrentGame()`，在释放单实例所有权前完成。
   保留后台/隐藏/取消逻辑。失败不阻止退出，但尝试关闭启动绑定并返回 false 供现有日志记录。
3. 独立打包 `app/game_startup_watcher.py` → `XingtuGameWatcher.exe`，建议独立 onefile，windowed、asInvoker、
   `uac_admin=False`；依赖仅 Python 标准库、两个 game_startup 模块与 packaged_product_identity 的固定身份常量。不可沿用主程序管理员 manifest。
   helper 与固定主程序 `星图.exe` 位于同一安装目录。主程序 spec 的 requireAdministrator 不变。
4. Setup 带入 helper。升级前记录准确 Run 值是否已开启，调用旧 helper `--disable` 并等待退出，
   再替换文件；只在旧状态确实开启时恢复新路径的 Run 值，不给关闭用户自动开启。
   卸载前同样停用并等待，删除本安装的 Run 项。不能只删文件留下活 watcher 或启动项。
   `--disable` 为当前 HKCU，不跨用户修改；以其他管理员账号安装/卸载的用户范围必须另行验收，
   不得把管理员账号 HKCU 当作原用户。服务与 `--disable` 比较进程与 Shell token SID；
   账号不同时关闭功能/返回非零，不声称修改原用户。保留本地小型 episode journal，避免重复提示。
5. 还需检查启动项被 Windows 设置/安全软件禁用的场景。此版本开关反映 Run 注册，不是系统保证
   已允许运行；安装文案不能把已注册写成已运行。启动项延迟属于 Windows 行为。

## 验证边界

Linux 离线 unittest 覆盖标题、首次观测、重复窗口/多进程、PID 复用、UAC 取消后重启 watcher、
显式退出、unknown、disable 竞态、写失败、坏状态和轻量依赖。它们不是 Windows 实机证据。
发布前需在 Windows 验证真实 x64 API、普通用户 Run 登录、真实 UAC 接受/取消、管理员主程序
存在检测、退出/重新开游戏、多会话、升级和卸载，以及启动项被禁用的提示。分别报告这些结果，
不把源码集成/单测当成 Setup 或游戏通过。

参考：Microsoft Run/RunOnce Registry Keys、ShellExecuteW、GetProcessTimes、
ProcessIdToSessionId、CreateToolhelp32Snapshot。Run 项可能延迟，ShellExecute 只请求正常 UAC。

主程序仅在当前 token SID 与交互 Shell SID 相同且可读取时开放开关；凭据提示输入另一个管理员账号时明确不可用，不会写错用户的启动项。普通 watcher 不需要管理员权限。

冻结路径以 `Path(sys.executable).resolve().parent` 为安装目录，不以 `_MEIPASS` 临时解包目录定位主程序。生产 Qt 名称直接引用 `packaged_product_identity.QT_EXE`，契约测试同时核对 build_qt_client.py production product 与 qt-client.iss；不使用旧 Tk 的 LEGACY_EXE。

## 本轮接线与安装器控制

主程序已注册 `gameStartupService`，仅真实生产模式（不含 probe）开放；只有明确选择“退出”才在释放实例前记录当前游戏身份。默认关闭时，退出也不写注册表。后台/隐藏/取消不抑制游戏。`tools/qt-game-watcher.spec` 独立 onefile，并在构建时读取真实 PE 验证 asInvoker；生产 build 接线由资源构建任务合入。

安装器共享 `tools/qt-game-watcher-lifecycle.iss`，事件接线由安装/卸载分支合入，不能把 include 文件存在当作已接线。CLI：

- `--status`：只读，0=关闭、10=Run 值精确匹配且已开启。
- `--disable`：删除且复核本安装的准确 Run 命令，再最多等待 15 秒，枚举进程映像路径确认其他 watcher 已退出。排除本次控制命令自己的 onefile 父子进程。等待时间到或路径读取 unknown 返回 6，不能用固定睡眠充当退出证据。
- `--restore-after-upgrade`：仅当安装器先前捕获 status=10 时调用，组件齐全、同账号且写入复核后返回0。新安装、原关闭状态不会调用。取消/升级失败应在旧文件恢复后调用，已开始原生卸载不恢复。
- 其他退出码：2=参数无效，4=读写/校验失败，5=当前token与桌面Shell账号不同或不满足范围。异常不冒称清理成功；不同于本安装精确命令的同名 Run 值不会被删除。
- 正在其他会话/账号运行的同安装 helper、未知权限、尚未处理完的 UAC 都可能阻止替换。只检查、不杀进程、不改其他账号 HKCU。要求关闭相关会话/提示后重试。
- 捕获后改变安装目录失败关闭，退出安装后在原路径恢复已开启状态，再从新目录重新开始。取消卸载、备份失败或资料移位失败时，必须恢复原 opt-in；恢复失败明确提示用户核对设置。
- 卸载停用登录绑定，但保留 LocalAppData 的小型防重复 journal。它和其他账号注册表不属于安装目录内“清除资料”的范围，不承诺全机器零残留。

模拟测试还覆盖默认关闭无注册表写入、控制命令状态、等待真正退出、unknown超时、其他账号拒绝、精确路径/onefile父子识别、恢复后验证及改动过的Run命令保护。控制CLI不触碰当前云端系统设置；Windows ABI、Setup事件和真正的登录/UAC仍待实测。
