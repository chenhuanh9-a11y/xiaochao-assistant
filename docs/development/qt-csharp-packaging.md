# Qt / C# 程序打包与覆盖更新

正式程序保持 `production` 身份与原 Qt 启动入口。C# grid 由同一源码 checkout 使用 .NET SDK `10.0.403` 离线发布为 `win-x64` 自包含组件，位于 `program/_internal/csharp-grid/StarMap.Grid.exe`。Python 模块由 PyInstaller 收集，数值服务不依赖用户另装 .NET。构建记录、release report、程序库存包含组件来源、运行库资产及 .NET 许可文件。

程序 Setup 沿用当前仅程序策略：`PROGRAM_UI_RESOURCES`、帮助资源与运行时属于程序；`managedBaseManifest/managedBaseBundleId/managedBaseManifestSha256` 为 `null`，资源动作是 `preserve`。独立基础包、作者路线、catalog、原始导入包和用户设置不进入程序替换清单；已退役的 QtIFW 内置地图参数与 `install-maps` helper 入口保持禁用。

当前候选版本为 `1.1.5`，PE 数字版本为 `1.0.0.27`。先以 `build_qt_client.py --instance production --stage-only` 从同一远端 commit 冻结程序，再以 `build_qtifw_trial.py --release` 生成最终原生 QtIFW Setup；传入冻结的 `--stage`、`--build-record` 及 QtIFW `--ifw-bin`。这条交付链不要求 ISCC，不生成 Inno 中间 Setup 或外层包装器；旧通用 Inno 构建入口保留兼容。`--upgrade-capable` 仅保留为兼容参数，正式 QtIFW Setup 总是包含离线更新支持。不得用未提交源码或旧 payload 冒充最终构建。

最终 EXE 由官方 QtIFW `binarycreator` 构建，使用复制到本轮输出的官方 `installerbase`，仅将 Setup 清单设为 `requireAdministrator`，延续此前正式 Setup 的 UAC 要求。程序压缩包由 `repogen` 先生成，同一 `program.7z` 直接用于 Setup 原生组件归档；额外 QRC 只放辅助工具和仓库元数据，不重复放程序压缩包。更新时先从 QRC 提取工具及 `archive-layout.json`，官方 `devtool dump` 从当前 Setup 读出唯一程序归档，补全本地离线仓库；QtIFW 自身继续核对仓库归档校验值。`update.rcc` 及新 controller 随维护更新持久化，维护器使用已安装的当前 helper，不依赖原 Setup 的临时目录。

已有安装发现只读枚举 HKCU/HKLM 的两种注册表视图。登记路径、卸载命令、owner、生产 profile、组件记录和发布版本先筛候选；唯一正在运行的生产根优先，否则使用唯一最高已装正式版本。最终选中候选仍须通过完整旧程序字节与新路径碰撞检查，不能用元数据阶段授予目录所有权。多个同等最高有效安装才显示明确候选供选择；坏登记显示具体原因，缺失历史目录不阻断新装。自动发现有效旧根时直接进入更新页，跳过目录选择；真正新装保留目录页，显式 `TrialTarget=...` 继续优先。手选旧根与自动发现使用同一更新页，验证目录不执行更新。

更新页只交接已有 QtIFW 维护器，原维护器加载真实旧操作数据库并执行原生组件更新；外层 Setup 绝不对旧根执行 fresh `runInstaller`，不会以一个 Execute 操作覆盖旧撤销数据库。程序和数据组件均在 `beginInstallation` 拒绝误入的新装事务：这是 SDK 清空组件数据库之前的钩子，`createOperations` 中仍保留后置防护。自定义页先加入，再隐藏原生事务页，避免引导页因缺少后续页而无法加载组件。更新成功后页面明确显示已完成及关闭入口；失败显示原因并可重试，重复点击不能并发或再次执行已完成的更新。官方 QtIFW 4.11 controller 没有成功状态赋值接口，因此该交接前端关闭时返回 **3（Canceled）**；这与维护器实际更新的退出码分开记录，helper 的错误码 2 也不是该原生退出码。不得把外层 3 写成成功 0，也不得仅据外层状态宣称更新失败；成功必须有实际维护器、组件、程序、持久 controller 和 Windows 版本登记共同验证。

原生更新和持久维护器验证各使用本轮备份目录下独立的 `--cache-path`，避免与仍在运行的 Setup 争用默认 QtIFW cache。QtIFW 正常将路径保存到 `network.xml` 时，普通维护可以继续使用仍保留在本安装备份目录下的缓存；验证子进程也可能写缓存和诊断回执，不称为完全只读。全清仅覆盖本安装根及本实例登记，不删除或接管用户历史全局缓存锁。新装仍是原生 Setup 自身的正常安装事务。

覆盖支持具有完整 QtIFW owner、生产 profile、真实程序库存及维护记录的 `1.1.2`、`1.1.3` 和 `1.1.4`。旧 `1.1.2` 的完整 managed 布局和仅程序布局均可接入；更新先保留旧程序和维护元数据备份，随后由 QtIFW 更新组件及 Windows 卸载版本。未知程序路径碰撞、改动字节、其他 Setup 的 pending、未完成资源事务均明确拒绝。运行时继续受 `work/pending-installer-upgrade.json` 阻断；只有同一 Setup 核验物理根、owner 和自身记录后才能继续。外置资源根 `%LOCALAPPDATA%/Xingtu/resources` 已存在时，更新持有其资源读锁并检查独立 catalog/删除事务，不借程序标记跳过资源恢复。

定向验证入口是 `tests/test_qt_csharp_grid_package.py`、`test_qtifw_upgrade.py`、`test_qtifw_trial.py`、`test_qtifw_installation.py`、`test_qtifw_native_ui.py` 和 `test_qtifw_bootstrap_packaging.py`。设置 `PYTHONPATH=app;tools;tests;experiments/qt_quick`；真实 C# 发布测试另设 `STARMAP_DOTNET`。普通升级 fixture 运行实际文件系统适配器，仅替换原生 QtIFW 进程；目录发现仅替换注册表/进程观察，保留真实文件准入；UI 回归在 Qt JS 引擎执行生产 controller/component，替换 UI/进程边界，并隔离两种脚本上下文。编译 fixture 使用真实 QtIFW 编译器、PE `.text` 与官方 base 对照以及真实 devtool 读取，不运行 Setup 或写 ARP。Windows 隔离安装/更新/卸载须另记实际命令和唯一 fixture GUID；这些证据不等于用户安装目录覆盖、可见 GUI、游戏或用户验收。

旧 managed 版首次启动生成的活动品牌中，仅 `xingtu.ico` 与 `xingtu-round.png` 可迁为新程序拥有：旧 QtIFW 库存必须拥有完整有效的 managed manifest 和对应候选 payload，候选声明、候选实际字节及活动实际字节一致；两文件先进入备份，其余未知路径继续拒绝。原生中断后每次重试都核对程序目标的旧/新合法中间态；缺文件可由同一更新继续补齐，已有文件必须精确匹配旧或新库存，未知字节、额外链接或被普通文件占用的目录路径均在原生执行前拒绝。已移除的旧候选证明从原始备份读取，不以 pending marker 推定目录所有权。
