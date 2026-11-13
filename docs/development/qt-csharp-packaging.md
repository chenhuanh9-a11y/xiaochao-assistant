# Qt / C# 程序打包与覆盖更新

正式程序保持 `production` 身份与原 Qt 启动入口。C# grid 由同一源码 checkout 使用 .NET SDK `10.0.403` 离线发布为 `win-x64` 自包含组件，位于 `program/_internal/csharp-grid/StarMap.Grid.exe`。Python 模块由 PyInstaller 收集，数值服务不依赖用户另装 .NET。构建记录、release report、程序库存包含组件来源、运行库资产及 .NET 许可文件。

程序 Setup 沿用当前仅程序策略：`PROGRAM_UI_RESOURCES`、帮助资源与运行时属于程序；`managedBaseManifest/managedBaseBundleId/managedBaseManifestSha256` 为 `null`，资源动作是 `preserve`。独立基础包、作者路线、catalog、原始导入包和用户设置不进入程序替换清单；已退役的 QtIFW 内置地图参数与 `install-maps` helper 入口保持禁用。

当前正式版本为 `1.1.4`，PE 数字版本为 `1.0.0.26`。完成 `build_qt_client.py --instance production` 的同一远端 commit 冻结后，以 `build_qtifw_trial.py --release --upgrade-capable --iscc <ISCC.exe>` 生成最终 Setup；传入该冻结的 `--stage`、`--build-record` 及 QtIFW `--ifw-bin`。包装器只在自身临时目录提取支持工具，再调用 QtIFW 原生安装或离线维护更新。不得用未提交源码或旧 payload 冒充最终构建。

覆盖支持具有完整 QtIFW owner、生产 profile、真实程序库存及维护记录的 `1.1.2` 和 `1.1.3`。旧 `1.1.2` 的完整 managed 布局和仅程序布局均可接入；更新先保留旧程序和维护元数据备份，随后由 QtIFW 更新组件及 Windows 卸载版本。未知程序路径碰撞、改动字节、其他 Setup 的 pending、未完成资源事务均明确拒绝。运行时继续受 `work/pending-installer-upgrade.json` 阻断；只有同一 Setup 核验物理根、owner 和自身记录后才能继续。外置资源根 `%LOCALAPPDATA%/Xingtu/resources` 已存在时，更新持有其资源读锁并检查独立 catalog/删除事务，不借程序标记跳过资源恢复。

定向验证入口是 `tests/test_qt_csharp_grid_package.py`、`test_qtifw_upgrade.py`、`test_qtifw_trial.py` 和 `test_qtifw_bootstrap_packaging.py`。设置 `PYTHONPATH=app;tools;tests;experiments/qt_quick`；真实 C# 发布测试另设 `STARMAP_DOTNET`，包装器编译 fixture 可用 `STARMAP_TEST_BRAND_ICON` 指向字节已校验的品牌 ICO。升级 fixture 运行实际文件系统适配器，只有原生 QtIFW 提取/组件登记进程被替换；坏 PE 测试使用真实 Windows loader，包装器测试只编译、不运行 Setup。这些证据不等于真实覆盖安装、GUI、游戏或用户验收。

旧 managed 版首次启动生成的活动品牌中，仅 `xingtu.ico` 与 `xingtu-round.png` 可迁为新程序拥有：旧 QtIFW 库存必须拥有完整有效的 managed manifest 和对应候选 payload，候选声明、候选实际字节及活动实际字节一致；两文件先进入备份，其余未知路径继续拒绝。原生中断后每次重试都核对程序目标的旧/新合法中间态；缺文件可由同一更新继续补齐，已有文件必须精确匹配旧或新库存，未知字节、额外链接或被普通文件占用的目录路径均在原生执行前拒绝。已移除的旧候选证明从原始备份读取，不以 pending marker 推定目录所有权。
