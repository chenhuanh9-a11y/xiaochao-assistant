# 旧安装资料迁移协议

本协议属于「安装与更新」，相邻影响「地图资源」和「自定义图库」。它规定旧 QtIFW 转 Inno Setup、以及 Inno 跨版本升级共用的纯资料迁移接口。不得把主动卸载的全清策略用作升级清理策略。

## 当前 helper 接口

`app/legacy_migration.py:migrate(source, program, identity=...)` 仅复制，不改写或删除来源。`source` 必须是已核实的旧产品安装根，`program` 必须是全新的目标程序目录。`identity` 必须由安装器的只读原生探测器提供，精确包含：

- `sourceKind`：`qtifw` 或 `inno`；`product`、`appId`：正式产品身份；测试产品一律拒绝。
- `installRoot`、`dataRoot`：实际来源安装根和 `work` 用户资料根。
- `uninstallRecordSha256`、`maintenanceToolSha256`：已核验卸载登记与维护工具的摘要。

探测器须同时验证产品名称、实际安装根、维护工具/卸载程序和完整卸载登记；未识别格式、路径不一致或无权限都必须失败关闭。生产接线在本文末尾说明；复制和新安装核验均不等于已经接管旧卸载器。

## 复制清单与归属

helper 仅纳入正式产品身份下的 `work` 用户资料：设置、地图记忆与收据/快照、自建路线、自定义图库，以及图包文件、收据和依赖。旧图包收据与载荷写入 `work/map-packages/legacy-unverified/` 隔离区，不会成为新版本可直接使用的活动收据；须经过现有「地图资源」准入流程后才能激活。每个普通文件记录大小与 SHA-256；清单绑定来源根摘要，日志不存绝对路径或用户文件名。来源依据是经安装器核验的身份描述与来源目录本身；旧文件不因此获得新的导入、地图身份或显示授权。

失败事务标记、活动导入锁、重解析点、硬链接、特殊文件、损坏资源收据或无法闭合的依赖使迁移停止。不会猜测旧 QtIFW 卸载行为，也不执行旧卸载器。复制接口始终保留来源；只有本文末尾单独的退休动作能够凭绑定的原生库存处理已核实的旧对象。

## 状态与恢复

先完整盘点，再将文件复制到目标旁的私有暂存树；文件和清单逐项校验后，以不覆盖的目录改名一次性发布 `work`。日志状态为 `pending`、`publishing`、`complete`，记录清单哈希、文件哈希、来源路径摘要、身份描述摘要和稳定错误码，不保存原路径、文件名或卸载记录内容。来源变化、目标非空、路径逃逸、链接、权限/占用、冲突、坏日志、取消和中断都不得报告成功；保留来源和恢复证据。重试只允许同一来源、同一身份、同一清单继续，目标内容不匹配则拒绝覆盖。成功调用可幂等核验，不能据此删除旧来源。

## 交给安装器接线的顺序

1. 读取 QtIFW 产品身份、实际安装根、用户资料根、卸载记录与维护工具；未知历史卸载行为时停止，不启动旧卸载。
2. 暂停游戏启动监听与维护工具，等待退出，再核验目标为空和两边资源事务都无待恢复状态。
3. 持久化本协议要求的事务身份与迁移清单，运行 helper，校验发布后的清单和每个文件哈希。
4. 只有迁移完成且原生 Inno 安装/卸载登记、快捷方式与新程序全部核验后，才允许接管旧卸载入口；失败时保留原件、暂存和日志，禁止伪称回滚成功。
5. 父进程绑定的退休动作再次核验新安装、全部迁移提交与旧对象后，逐项移除旧实例及精确暂存。复制接口本身不拥有这项权限；正式发布前仍须本机验收。
6. 维护工具应在迁移结束并核验完成后才退出；中断恢复先读原事务日志，不得重新猜测身份或重做成功复制。

## 兼容位置参数入口（只准备，Inno 事件不再调用）

位置参数 `star-map-reset-helper legacy-prepare` 调用 `installer_legacy_bridge`，仅核验和复制，不拥有卸载、登记写入、原生提交或旧入口退休权限。保留它的独立检查，不用它替代下面父进程绑定的生产动作。

来源探测只认唯一的当前用户正式产品登记，读取完整登记值及类型，并绑定维护工具实际路径和摘要。HKCU 的共享视图只枚举一次；机器登记、歧义、未知产品/工具或布局都停止。仓内 QtIFW 的登记/维护根和 `program` 程序根分开核对：传给离线核心的 `installRoot` 是实际程序根，`dataRoot` 是其 `work`；卸载登记仍必须指向外层维护根，摘要绑定这一完整登记。复制期间用 Win32 句柄阻止已映射程序/维护工具的并发使用或重新启动，不主动结束程序或监听器。

旧资源操作留下的根 `import.lock` 允许在实际取得已有文件的排它 OS 锁后迁移；不创建、删除或改写来源锁。占用、链接、硬链接、嵌套同名锁、新出现的锁和目标独立锁仍拒绝。复制、来源复验、发布与完成日志的整个过程都持锁；完成后同一来源可以重新校验。崩溃遗留的迁移锁或撕裂日志仍需人工核对，不能自动清除证据。

副本位于新目录的 `.legacy-upgrade-prepared`，尚未成为新安装活动资料。兼容接口回执明确是 `prepared-native-unverified`，并写明 `nativeInstalled=False`、`oldEntryRetired=False`、`sourceDeleted=False`。它不授予原生安装权限。旧 Inno 同根升级继续已有 preserve 协调器，不经过这条搬迁入口。

### 外置资源与编辑器图库的资产清单

准备入口在唯一当前用户产品登记和来源程序根已核实后，只扫描以下固定根；不会按目录名搜索用户磁盘，也不认领其他用户目录：

| 根标签 | 来源 | 目标相对路径 |
| --- | --- | --- |
| `external` | 当前用户已知文件夹下的 `Xingtu/resources` | `work/map-packages/legacy-unverified/asset-roots/external/` |
| `outer-custom-maps` | 旧程序根下的 `assets/custom-maps` | `work/map-packages/legacy-unverified/asset-roots/outer-custom-maps/` |
| `internal-custom-maps` | 旧程序根下的 `_internal/assets/custom-maps` | `work/map-packages/legacy-unverified/asset-roots/internal-custom-maps/` |
| `editor-base` | 旧程序根下的 `_internal/qt-editor-base` | `work/map-packages/legacy-unverified/asset-roots/editor-base/` |
| `imported-resources` | 旧程序根下的 `work/imported-resources` | `work/map-packages/legacy-unverified/asset-roots/imported-resources/` |

所有这些来源均使用同一复制引擎，纳入同一逐文件哈希清单和恢复日志。完整目录结构、图包载荷、索引、收据和依赖字节原样保留在隔离区；旧收据不复制成活动收据，也不因此取得地图识别身份、显示授权或默认资源资格。外置资源根存在时必须已有 `work/map-packages/import.lock`；入口与迁移引擎都实际取得相应排它锁并持有至复验、发布和完成日志结束，不移除或改写来源锁。产品程序根及外置根按现有用户身份边界校验；未知根、缺锁、坏收据、待恢复事务、链接、硬链接、越界路径或来源变化均失败关闭。

交给第 4 项原生安装接缝的稳定准备回执包括 `status=prepared-native-unverified`、`copiedFiles`、`assetRoots`（固定标签列表）、`inventorySha256`（清单摘要）、`nativeInstalled=False`、`oldEntryRetired=False` 和 `sourceDeleted=False`。只有全部文件和根集合复核一致才可产生准备成功；接收方仍须按「地图资源」现行准入流程审查隔离的图包，不能据此写活动收据。失败回执含稳定 `errorCode`，不导出绝对路径或用户文件名。此合同只代表资产准备完成，不代表 Inno 已安装、旧入口已退休或升级事务已提交。

PR #151 的成套复制与交接摘要在 PR #152 基线上精确整合，未整文件覆盖另一切片。生产适配器复用该复制核心及已有检查；退休作为单独动作接在资料提交之后，不调用历史卸载器。

源码和合成目录测试不能代表 QtIFW / Inno 已安装或 Windows 联合验收通过。真实登记、历史包、父 lease ABI、原生退出后的全清核验、占用统计及 huan 本机正式验收均须另外提供证据。

## 第 5 项生产资产适配器与 Inno 接线

新增 `installer_legacy_handoff.LegacyHandoff`，通过当前 reset helper 的
`--action legacy-*` 路由进入。它与上述位置参数 `legacy-prepare` 是不同权限的入口：
只有现有 `_target` 校验、当前正式产品身份、当前用户和真实借用的安装器父进程资源锁
全部核实后，才构造事务协调器。命令行没有资源适配器或原生观察替身开关。

| 动作 | 真实接线与恢复边界 |
| --- | --- |
| `legacy-prepare` | 核实 QtIFW 来源及关闭句柄，调用完整资产适配器并复验；独占写入来源、用户、目标、程序库存及资产清单绑定的准备计划；复用安装 journal 持久化 `prepared`。丢回执可按原会话重读，不重新复制。 |
| `legacy-start-native` | 重读当前包库存及准备清单，通过既有 journal 转到 `native-in-progress`。重复开始、取消和单独回滚均拒绝。 |
| `legacy-native-complete` | 使用既有 native observation 实际读取登记值和类型、完整程序/所有权库存、卸载器及快捷方式；保存完整原生凭据。重复完成只复验原凭据，不收下调用方自报的成功。 |
| `legacy-commit` / `legacy-resume` | 从原凭据恢复上下文，重新核验原生状态，才允许适配器提交资料。丢回执先核验已提交字节；部分提交由适配器按同一清单向前续办，不重跑原生安装。再次核验后独占保存资料提交记录。 |
| `legacy-retire` | 只在新原生与资料提交均再次核实后退出旧实例并清理精确暂存，完成后才清 pending。 |
| `legacy-status` | 未完成时只读 journal；完成时只读复核新程序、迁移资料与旧对象确已退出，不执行或补写收尾。 |
| `legacy-cancel` | 只允许原生开始前取消，保留来源、副本和证据；取消为终态，再试需选择新的空目标。开始后的不确定结果保留为 `native-in-progress`，不得猜测回滚。 |

安装 journal 增加明确的 `resourceAction=legacy` 类型，避免普通 preserve/reset 恢复
跳过迁移资料核验而清除阻塞。现有旧 Inno 同根升级仍使用原来的 `preserve`。
迁移成功提交资料后也只报告 `new-install-verified-old-entry-pending`，原有 pending 标记
保持，`installerFinalized`、`upgradeComplete`、`oldEntryRetired` 均为假。普通协调器和
低层 `finish_complete` 都禁止结束这种事务；只有退休协调器实际回读通过后，独立的
`finish_legacy` 才能结束它。失败保留 pending，不因文字状态或调用方布尔值放行。

生产工厂返回 `installer_legacy_assets.CompleteAssetTransfer`，实现真实多根
`prepare`、`verify_prepared`、`commit`、`verify_committed`。设置与图库路线沿
`RuntimePaths` 的 `work/nightmare-memory`、`work/seasons` 等原布局发布；固定图库素材、
图包载荷、收据、依赖与历史快照在同一份清单中保留，仍放在隔离区。
回执明确区分 `no-map-resources` 与 `reimport-required`，且 `resourcesActivated=False`。
未导入基础包时没有对应模式可用，旧格式图包须按现行资源页规则换成新版导入；这里不恢复
活动收据、不调整兼容规则、不种默认地图，也不承诺迁移后旧地图已可用。

资产操作每次从当前来源和准备/目标文件重新读取字节、大小、原文件身份、根集合及依赖。
生产实例枚举独立于按来源筛选的登记探测：外置共享根有其他正式实例或机器登记时拒绝；
仅允许已绑定的旧来源及本事务新目标。同一当前用户的目录所有权和各根的原有资源锁都须核实。
准备对象的文件身份在原生开始前绑定进计划；资料提交仅将这些对象无覆盖改名到最终位置。
同名字节相等但身份不同的目标也拒绝，不根据可编辑完成标记认领。发布中断与丢回执按原清单续办。

`PrepareToInstall` 先检查路径和登记边界，再取得目标资源锁，调用 `legacy-status` / `legacy-prepare`。
非空目标只获得检查机会：Python 仍拒绝未知内容，仅接受与来源、用户、会话、目标及库存绑定的
本事务准备目录和锁目录。`ssInstall` 在任何原生写入前调用 `legacy-start-native`；
`ssPostInstall` 调用 `legacy-native-complete`、`legacy-commit`，最后 `legacy-retire`。这些均使用当前包校验过的助手、
相同父进程/借用锁和独立回执。回执再次核对安装包、库存、父进程、用户、会话和清单。
新进程续办发现原生开始过，只可 `legacy-resume`：凭据丢失时从真实登记读取卸载器，完整重新
采集和核验当前程序、登记及快捷方式；不足就停止，绝不重复原生安装。
Inno 时序依据 [官方事件文档](https://jrsoftware.org/ishelp/topic_scriptevents.htm)。

复制前原子保存事务意图，计划与提交记录均先完整写私有对象，再无覆盖发布。复制核心的可变日志
同样先同步私有对象再替换原日志；撕裂私有写保留但不作为状态。journal 初代在私有目录完整生成后
才公布会话目录，未发布的撕裂目录保留；完整初代仅丢 pending 发布时可验证并补原指针。
未知/损坏的已发布记录仍拒绝。首次意图本身未能完整发布且留下未知根文件时仍须核对，不认领未知内容；
不承诺多文件断电原子性。

资产扫描/哈希/复制按单调期限检查；Inno 沿用 PR #150 的精确子进程句柄，125 秒超时后只终止
该次同步助手，并确认进程结束。没有子线程或后台写入者；未确认停止时拒绝重试，维护互斥保持。
仅新安装与资料提交成功时仍保留 `old-entry-pending` 和非零整体结果；旧入口与暂存确认清理后
才返回完整完成。没有自动启动程序；升级恢复与主动全清卸载仍分开处理。

生产路径测试调用实际助手参数解析/路由和完整资产适配器，临时目录执行真实多根复制与无覆盖提交；
原生登记、当前包测试元数据、用户/借用资源锁及原生观察文件后端为窄 fixture。
生产观察器拒绝 `fixture-only` 凭据，CLI 没有 fixture 选项。源码和测试不代表真实 Windows 安装验收。
已有卡片 30、39 的本机登记和检查入口阻塞仍由原会话续办，不重复试写、提问或结案。

## QtIFW 逐项退休与精确收尾

支持范围是仓内 `build_qtifw_trial.py --release` 生成的 portable 嵌套布局：维护根包含
`trial-maintenance.exe/.dat`、两个版本为 `1.0.0` 的组件、根 owner，程序位于 `program`，
支持工具的嵌入策略位于 `_installer-support/_internal/qtifw-trial-policy.json`。
策略、owner、正式程序 profile、release report 与程序逐文件库存须相互一致。
未提供可证明归属的旧平铺/其它维护数据格式没有破坏权限，明确保留未完成原因。

不能从 `_installer-support` 的名字推断其所有文件都归安装器。只读解析实际 `.dat` 的
Extract/Copy/CreateShortcut 记录，Extract 引用的 `installerResources` 文件是二进制
QStringList；逐条还原原生文件与快捷方式库存。解析器只支持已核实的 Windows 小端
维护数据布局，拒绝未知操作、坏偏移/计数、缺失清单、Copy 循环、未展开变量与越界。
格式依据是 QtIFW [binarycontent.cpp](https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/installer/binarycontent.cpp)、
[fileio.cpp](https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/installer/fileio.cpp) 和
[extractarchiveoperation.cpp](https://github.com/qtproject/installer-framework/blob/4.11.0/src/libs/installer/extractarchiveoperation.cpp)。
仓内构建器/组件脚本证明生成布局；已有 real-trial 文档只证明当时的安装工具实验，不能替代生产历史包验收。

原生开始前，计划绑定原维护/程序根、当前用户、完整旧登记所有值/类型及修改时间、工具、
原生元数据、全部来源文件/目录与快捷方式身份及摘要；设置与资源须有相应迁移提交。
即使原生 Extract 曾创建过资料文件，也不能绕过迁移清单删除其后来内容。
未知文件、未迁移资料、额外目录、变化、其他用户、链接、硬链接和占用都停止。
外置根独立枚举全部正式登记，其他安装或机器登记意味着共享而拒绝；只处理已迁移文件，
保留原来的外置根及其常驻空锁目录，不删除整个共享根。

收尾先从 journal 复核当前新程序、登记、卸载器、选定快捷方式和每个资料目标；持有
禁止改写/替换的新文件句柄，并保留旧映像排斥、原有资源字节锁和维护互斥。
原生观察器的共享读例外只由当前活着且路径一致的保护句柄授予，不能用命令行开启。
新安装未选但旧原生库存拥有的图标，明确在原生凭据里绑定原文件身份与本计划，交给退休
阶段处理；普通未选图标仍完整观察，任何变化仍拒绝，不能用这项绑定跳过新安装选定图标。
再把同一个小型计划移入永久事务历史，发布恢复定位与完整退休意图，然后逐项核验、
写意图、按同一句柄删除、确认消失并记账。登记用精确对象句柄和原始值比较后删除，
不重新执行旧维护工具，不按名称删除后来同名的对象。

退休意图发布后，`legacy-resume` 只使用原绑定证据和当前新安装/资料证据继续，
不再要求已经删除的旧登记/维护工具仍存在。同名替换拒绝，丢单项回执可凭原意图与实际
缺失继续。原生开始后的恢复不再次安装。只有来源、旧入口、维护工具与本事务精确暂存
实际消失后才将 journal 完成并清 pending；原始小型计划、意图、每项记账和原生凭据作为
必要事务证据永久保留，迁移资料备份与准备树不保留。撕裂私有写和无法证明归属的残留
不会隐藏或按通配符删除；不承诺多文件断电原子性。

验证分为官方格式合成 `.dat`、真实临时 Windows 树/句柄、实际助手参数路由，以及仍未完成
的真实历史包/Windows 原生安装验收。登记、用户/父进程和 native 观察后端的窄 fixture
均须标明，生产没有 fixture 开关。未读取本人旧安装，没有试写受阻登记，没有使用 PR #148
的验收工具；没有本提交已安装 Inno 编译器时，不绕过仅限托管环境的编译入口。
