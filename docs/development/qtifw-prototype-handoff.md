# Qt Installer Framework 原型交接

当前本地真实 payload 的接续结果见 [试用候选交接](qtifw-real-trial.md)。本文仍保留云端原型当时的未测项与证据边界。

本地 Windows 后续证据及迁移方案见 [Windows 隔离原型](qtifw-windows-prototype.md)。以下保留云端交接时的范围和未测项，不以新证据改写旧结果。

## 当前范围

基线：`14405ee6907354a473b99de44e0e564ad96d7522`。这份交接只有独立合成生命周期脚本、7 项源码测试和已有结果，不改变星图安装链。按用户要求停止继续云端开发，交由本地 Windows 工作区接续。

它不是星图安装包，也不是完成的 Inno 迁移。没有操作用户安装、注册表、游戏或个人数据。合成版本 1.0.0/2.0.0/3.0.0 不是星图产品版本。

文件：

- `tools/probe_qtifw_lifecycle.py`：仅在全新根运行的 Linux 合成生命周期观察器。
- `tests/test_qtifw_lifecycle_probe.py`：7 项源码级测试。
- `docs/development/qtifw-prototype-evidence.json`：现有最后一轮原始结果，含文件 hash 和退出码。
- `docs/development/qtifw-prototype-logs.txt`：选定原日志的路径归一化副本，开头记录原始日志 hash；无用户日志、地图、配置或二进制。

## 已经实际观察到的行为

工具是未修改的官方 QtIFW 4.11.0，Qt 6.9.3，Linux offscreen。不是 Windows 证据。

1. 新装合成 v1 成功；维护器更新 v2 成功；v1 独有程序文件被移除。
2. 更新至 v3 时，标准 Execute 操作故意返回 47，安装器返回 3。日志确证先撤销旧 v2，再提取 v3，失败后撤销新操作。**旧程序没有自动恢复**，维护器仍在。
3. 使用维护器 `install org.xingtu.qtifw.lifecycleprobe` 从已知 v2 本地仓库重装，退出 0，程序版本恢复 2.0.0。这是特定场景的前向修复，不是任意失败自动回滚承诺。
4. 默认卸载退出 0，组件程序移除；安装后创建的 work 文件、程序目录未知文件、根目录未知文件及外部资料哨兵保留。
5. 首次安装的真实 Execute 中断点被强制终止后，已有 payload，但尚无维护器。直接重试拒绝非空目录；未证明自动恢复，更没有断电重启验证。
6. 默认非空目录拒绝没有改动旧哨兵。仅在合成夹具明确允许非空后，自定义 legacy/collision 守卫也确实拒绝，未进入 Extract；框架可能写 InstallationLog.txt，因此不能声称整棵目录完全不变。

### 原始回执的已知解释修正

- `cancel` / `cancel-before-install.log` **不是正常用户取消**。管道输入 `n` 未被 QtIFW 消费，实际为“非终端缺少交互确认时拒绝”，退出 1，未写程序。正常 GUI/TTY 取消仍待测。
- `probeCompleted=true` 仅表示观察流程走完。脚本部分步骤记录布尔但未逐一断言，不能当所有产品验收通过。
- `legacyRejected` 是非空目录默认拒绝，不是 Inno 识别成功；另一个 `legacyGuardAfterConsent` 才证明自定义守卫执行，仍不是迁移。
- 修复后虽版本值正确，尚缺直接修复终态的逐字节 payload/维护器 list 独立回执；后续卸载日志的 sanity check 成功只能作为补充。

这些已知问题保留在交接中，不为赶交付改写旧日志或追加重复实验。

## 官方工具固定来源

Windows 4.11.0（只核官方元数据，本轮未下载/执行 Windows 工具）：

`https://download.qt.io/online/qtsdkrepository/windows_x86/ifw/tools_ifw_411/qt.tools.ifw.411/4.11.0-0-202603231357ifw-win-x64.7z`

- 56,804,453 bytes
- SHA256 `c47201c4f6a82a8b607daa245237f40831d78425e904edd1514b71fd17efefc1`

Linux 4.11.0（实际下载、核 hash 并执行）：

`https://download.qt.io/online/qtsdkrepository/linux_x64/ifw/tools_ifw_411/qt.tools.ifw.411/4.11.0-0-202603231357ifw-linux-x64.7z`

- 65,327,452 bytes
- SHA256 `79ef906970c7e4be9ec15447fd5acb0a8ae2ab41aef5f9184086ce3972eddbc7`

`installerbase --version` 可查版本；`binarycreator --version` 不支持，不能以其 usage 当工具损坏。

许可：核心为 `LicenseRef-Qt-Commercial OR GPL-3.0-only WITH Qt-GPL-exception-1.0`。本次内部运行未修改工具不需商业订阅；正式交付需保留 IFW、Qt、第三方许可和适用源码提供说明。例外并非无条件 MIT 许可。

## 最短复现与本地接续

从独立分支读取，不覆盖本地正在工作的分支。源码检查：

```text
python -m unittest discover -s tests -p test_qtifw_lifecycle_probe.py -v
```

Linux 完整合成观察器（全新输出目录；会终止它自己创建的合成进程组）：

```text
python tools/probe_qtifw_lifecycle.py --ifw-bin <已核官方Linux工具bin> --output <全新合成根>
```

该脚本主入口明确拒绝 Windows，不能直接在本机套用 Linux killpg 中断实现。Windows 可以先生成无故障的通用包模板并构建合成安装器，不执行安装：

```text
python -c "import sys; from pathlib import Path; sys.path.insert(0,'tools'); from probe_qtifw_lifecycle import prepare_packages; prepare_packages(Path('qtifw-local-probe/v1'),'1.0.0')"
<QtIFW-bin>\binarycreator.exe --offline-only -t <QtIFW-bin>\installerbase.exe -c qtifw-local-probe/v1/config/config.xml -p qtifw-local-probe/v1/packages qtifw-local-probe/synthetic-installer.exe
```

`qtifw-local-probe/v1` 必须不存在；先在本地核工具实际 hash。此命令只构建合成程序，不是星图 Setup。后续真实窗口、取消和故障测试只针对明确的新测试根和本次进程，不使用用户现有安装根。

## 星图 payload 接入：接口已核，适配器尚未写

不要再复制自建整树备份/回滚引擎。先复用：

1. `tools/build_qt_client.py` 的 `production_file_projection` 和 `write_installer_program_inventory`：获取精确程序与只读 managed-base candidate 文件，排除活动资源/editor。
2. `tools/rebuild_qt_installer.py` 的 `validate_managed_runtime_reuse`：核真实完整 build-record、候选 manifest、EXE、release report、profile、三个 helper 和 watcher。
3. QtIFW 组件只接收已验证程序 inventory，保持 EXE/helper 相对布局。保留只读 `_internal/qt-editor-base`；不能把 work、导入包、活动资源或用户自建文件打进组件。旧 `_installer-owned-files.txt` 不能自动授予 QtIFW 删除权限。
4. `client_paths.py` 当前强制 production 数据根与 EXE 同根；设置/导入状态在 work。本轮不改变这个跨模块合同。可先按组件所有权隔离程序与资料，物理迁移资料根是另一个变更。

## 必须留待本地实现与验证

- 真实 payload 适配器、正式版本/身份和产品工具链绑定。
- 可选“保留/清除资料”页面及有限范围的清理适配。`RemoveTargetDir=false` 只避免整根删除，不保护组件已经拥有的文件。现有 reset helper 不是全量用户资料卸载接口。
- 旧 Inno 迁移。QtIFW 的 UNDO 不能撤销已经执行的旧卸载器，旧卸载失败必须明确失败；不得直接接受非空旧目录并让 Extract 接管未知文件。
- 应用的路径/SID/进程身份、watcher、资源 lease 和 pending 恢复门仍需保留，不以按进程名 StopProcess 替代。
- Windows GUI 正常取消、更新取消、中断恢复、卸载与真实用户选择；不得把此次 Linux 合成结果写成通过。

官方说明：

- https://doc.qt.io/qtinstallerframework/ifw-tools.html
- https://doc.qt.io/qtinstallerframework/ifw-globalconfig.html
- https://doc.qt.io/qtinstallerframework/operations.html
- https://doc.qt.io/qtinstallerframework/ifw-cli.html
