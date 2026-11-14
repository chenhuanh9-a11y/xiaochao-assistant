# 开发与交付流程

GitHub 仓库是源码协作与交付的依据。本地完成的改动应及时提交并推送到对应分支，说明变更范围与验证结果；存在未通过的检查或尚未确认的行为时，在 PR 中明确记录。

1. 开始修改前获取远端更新，保留其他协作者的提交；使用普通推送，不强推覆盖他人工作。
2. 在功能或修复分支开发，完成针对性测试和代码复核。检查通过且属于已授权交付范围的 PR 再合并到 `main`；存在冲突、失败检查或新的范围变化时先处理并说明。
3. 打包优先从明确远端 commit 的干净 checkout 开始，记录提交 SHA、显示版本、构建参数和产物校验和。不要夹带未提交的本地源码。
4. 程序与地图资源分开管理。交付记录绑定资源版本、结构/索引格式及哈希，核对程序实际消费的资源；不能只凭文件名推定兼容。
5. 资源发布遵循授权范围和来源说明。游戏原包、账号信息、个人配置、日志、密钥及未授权素材不能随源码或构建产物提交。

离线测试应使用 fixture 或 mock；需要 Windows 输入、GUI 或游戏实测的功能，单独记录其验证状态。测试通过不等同于完成真实游戏验证。

## 变更规模与功能关系

根目录 [AGENTS.md](../AGENTS.md) 和 [PR 模板](../.github/pull_request_template.md) 提供短检查清单。普通小修只需在 PR 说明问题、影响链路、实际验证和未测项；不要为每次修复增加设计报告或跑无关全库。跨模块协议或状态归属变化才补必要设计。以下是当前代码导航，不代表各条链路已经完整统一或全部验收。

| 变更入口 | 必须跟到的消费者 | 现有代码与测试起点 |
| --- | --- | --- |
| 识别、正门/侧门、相似组 | 候选证据 → 组态/身份 → 当前帧验证 → 提交；暂定成员与唯一身份确认分清 | [map_identity_engine.py](../app/map_identity_engine.py)、[difficult_native_identity.py](../app/difficult_native_identity.py)、[test_native_similarity_runtime.py](../tests/test_native_similarity_runtime.py)、[test_front_gate_observation.py](../tests/test_front_gate_observation.py)、[test_side_entry_search.py](../tests/test_side_entry_search.py) |
| 首次/后续贴合、楼层、关闭与重开 | 当前地图/层/帧 → worker → 验证 → round-pose/core → 正确可见像素 | [client_live_runtime.py](../experiments/qt_quick/client_live_runtime.py)、[fast_structure_alignment.py](../app/fast_structure_alignment.py)、[alignment_pose_transaction.py](../app/alignment_pose_transaction.py)、[test_client_group_queue.py](../experiments/qt_quick/test_client_group_queue.py)、[test_client_session.py](../experiments/qt_quick/test_client_session.py) |
| 作者与自定义路线选择 | 偏好 → 实际来源与每层显示桥 → 渲染缓存 → 当前显示；身份/结构位姿不漂移 | [optional_author_displays.py](../app/optional_author_displays.py)、[test_client_route_preferences.py](../experiments/qt_quick/test_client_route_preferences.py)、[test_client_route_reprojection.py](../experiments/qt_quick/test_client_route_reprojection.py)、[test_client_custom_display.py](../experiments/qt_quick/test_client_custom_display.py) |
| 下载/导入资源 | 下载状态与校验 → 安装事务 → 资源工厂 → 作者选项/列表 → 缓存和旧任务失效 | [star_map_download.py](../app/star_map_download.py)、[star_map_import.py](../app/star_map_import.py)、[client_resources.py](../experiments/qt_quick/client_resources.py)、[test_live_author_import.py](../tests/test_live_author_import.py)、[test_client_import_upgrade_parity.py](../experiments/qt_quick/test_client_import_upgrade_parity.py)、[test_map_package_upgrade.py](../tests/test_map_package_upgrade.py) |

噩梦与困难应使用同一识别/贴合及生命周期实现，差异由资源、楼层元数据和必要参数提供。修改前核实际调用路径，修改后用两模式受影响的参数或代表资源验证。无法验证的资源契约要留明确缺口；不能删验证、复制模式专属算法或强套另一模式的门/坐标语义。既有测试名只是起点，例如排队首次任务测试不能代表后续每次开图延迟已经修复。

涉及异步与状态的改动，按风险选择成功、失败、取消、重复触发、迟到回调及换图/换层/关图/结束本局反例。检查最后实际消费者，而不只检查日志、列表条目或中间返回值。可重复的少量代表回归优先；不为小修添加镜像实现的测试或无关全量测试。

独立资源仓库的路线导入由 `ImportedResourceService` 保存后自动启用显示组件，同一包内的作者组件通过一次 catalog 事务提交；作者选择偏好仍由原显示设置维护。图库卡片携带实际 `packageId/resourceVersion`，删除入口与本地资源页共用同一个版本删除计划和确认。Service 在 UI 线程维护计划、稳定模型及消息，确认时取得 Session idle lease，后台 `imported_resource_removal` 在资源写锁内复核范围，停用受影响选择，再删除确认过的文件。删除结构版本会停用对应模式和依赖显示，其他作者包文件及原始导入包保留；未知、改动或替换对象保留并报告。文件移入暂存与 catalog 使用同一个既有 commit witness 决定恢复方向；恢复未完成时禁止资源消费和新写入。关窗排空已接受的写入，取消在提交前回滚，提交后完成清理；Windows 原生文件及空目录操作绑定已核对句柄。此恢复支持进程中断，不承诺断电原子性。

Qt 交互沿用持久页面和稳定卡片 ID：重复点击当前页/标签应幂等；普通状态变更只写发生变化的控件，不先隐藏整页/全列表再重新摆放。轮询可以读取最新状态，但状态相同就不重复写模型或控件属性、布局或图片。导入/删除等真正改变资源身份时才失效对应列表与预览缓存，保留其他区域的选择、焦点和滚动。核对卡顿时分别记录回调、布局/绘图调度和实际可见效果；仅缩短回调不能证明流畅。

生产导入资源默认位于 `RuntimePaths.installation_root / resources`，即与 `星图.exe` 同级的 `resources` 目录；原生 QtIFW 布局为所选安装目录下的 `program/resources`。结构和路线仍是独立组件，存放位置跟随用户选择的安装盘。普通升级保留目录和 catalog，彻底卸载清除本安装根内资源；不同安装各自持有资源。默认路径不依赖 `LOCALAPPDATA`，写入失败不得自动回落到 C 盘。

旧 `%LOCALAPPDATA%/Xingtu/resources` 仅作为显式迁移来源。资源页显示当前目录，发现旧库后可确认迁入；只向尚不存在的本地资源目录复制完整已保存版本及 catalog，不覆盖或合并已有本地库。复制、实际库存及选择校验在后台和旧库写锁内完成，在目标盘暂存后一次发布。发布前取消或失败保留源库；无法确认的暂存对象保留，不自动认领或当成可用资源。提交完成后刷新同一目标 resolver 的实际消费者。磁盘不足、占用、链接、坏版本或未完成事务均拒绝对应操作，不绕过完整性检查。

迁入会保留旧副本，因此仍占原盘空间。用户可另行明确选择“清理旧副本”；它只通过现有逐版本删除事务清理已在目标完整验证且源仍匹配的版本，未知、改动、未复制对象和小型历史/锁记录保留。其他旧安装可能共用旧库，界面须在确认前说明影响。取消清理不撤销已经提交的版本删除；未完成删除使用原有恢复流程。原始下载 ZIP 不随此操作移动或删除。安装器更新与卸载均保护本安装的实际资源根，不再以无关旧公共库的状态阻断当前安装。

## 四模式与资源独立化计划

S0/S1 困难、噩梦的资源外置及此前整合设计见 [四模式识别、贴合与资源导入统一方案](unified-recognition-resources-plan.md)。其中整合识别、竞态和 prepared solver 的设计属于历史方案；当前运行链按下面的原分支实现维护。

识别、贴合、楼层判断与轻提示恢复到 `codex/qt-csharp-grid-20261110` 的 `d7063612fc847067f58b71940071228721d79fe0`。默认程序入口使用 C# 网格候选搜索；Python 继续执行原分支的识别、精化和最终接受/拒绝。实际入口由 Qt Session worker 的 C# request scope 进入 `client_live_runtime.LiveRuntime` 及其原 `_AlignmentHost`；S0 噩梦使用原 `MapIdentityEngine`，其余模式使用原 `NativeDifficultCandidateEngine` 和原 S1 scanner。`RecognitionRace`、direct/local structure recognition、`StructurePoseSolver` 与 prepared inputs 不参与默认生产调用链；保留的历史单元或辅助定义不能作为接回它们的理由。C# 服务失败拒绝当前请求，后续请求可重新启动服务，没有自动切回 Python 网格的策略。

`client_original_providers` 和 original base/control loader 只负责将独立组件内的原始资源、参数和坐标交给上述原实现，并保留资源当前性及取消保护。基础包使用 `xingtu-original-qt-base-v1` 入口、原 typed/legacy 结构和原 fast 数据；作者图仍为独立显示组件。旧整合基础包保留在仓库时不会继续取得可用模式资格，须导入适配的新基础包及其对应路线包。模式、地图、楼层或资源变化的失效与 Qt 启动、页面、安装删除职责继续使用现有产品壳；正常及构造中途退出均在 Session worker 排空后关闭唯一 C# owner。

控件资源在制作阶段准备。S0 识别和结构包导出端使用程序内固定的 `app/control-data/s0-floor-templates-v1.npz`，不在用户 CPU 上重画或归一化模板；其显式生产工具及环境记录见该目录的 README。S1 保留原固定参数与精度。普通导出不运行字体生成器，缺失程序模板不自动重建。结构包发布前，出口实际解码并验证 S0 模板来源等价及全部 fast 派生；失败不产出可交付文件，不写用户 catalog。客户端启用只核对声明文件的完整性、格式、数组类型/形状/有效值、上下文、楼层、坐标及资源绑定，不再逐值比较另一份 S0 参考，也不调用 prepare 重算地图派生来授予可用性。原观察器参数、资源当前性及取消保护保留；该变化不放开任意观察器参数注入。精确数值自检属于制作端，不依赖用户硬件的复算结果。

工厂准备、普通 rank、Host.fit 和作者显示使用短期资源读取作用域：同次操作复用元数据与已解码的只读资源，新文件仍实际校验，结果发布前和作用域退出时复验实际消费的资源；作用域不跨帧保留准入权限。失败或变更的作者显示不能污染基础结构的可用性，仍按原规则报告并回退。普通准备和重试不弹警告；未成功且有真实失败原因时，在关图、取消或结束本局汇总一次。当前帧合法暂定贴合也算本次已贴合，仅抑制错误报告，不授予身份或显示权限；无 verdict 的短开取消不泛化为失败。C#／worker 异常立即可见，终局去重；下一开图及已知换层后的真实失败仍独立报告，旧帧／旧资源的迟到结果不能生成新提示。

地图资源卡片只读取原包声明和已登记文件，不初始化识别资源或预先解码全部结构与作者图片。卡片的本地覆盖数表示已安装声明；预览请求对应层时，仍通过真实 NPZ／图片、作者坐标和结构来源校验，返回前复验实际消费字节。损坏作者预览不授予任何身份能力；首页按原规则回退到经过验证的结构预览，基础结构或 catalog 失效继续拒绝。S0 默认楼层搜索边界若裁断高亮框，补取边界像素后重走原符号与几何验证；已明确的多列歧义不得由补边覆盖。

当前 Windows 回归以此用户要求为范围：旧 race/direct/local/prepared 求解、normalized 基础包、可任意注入像素算法参数及 proposal-only 编排的历史测试不再代表产品要求。其源码保留用于历史对照，不计入当前通过数。独立资源、显示缓存、事务、取消和后台队列测试须迁移真实 original 包 fixture；不能随算法一起退掉。`test_original_qt_resource_flow` 验证实际导出、保存、启用和工厂，楼层资源当前性由 `test_imported_s0_observation.OriginalS0ControlsLifecycleTests` 验证，C# 原数值组件及请求生命周期由 `test_hybrid_grid_backend`、`test_qt_csharp_grid_runtime` 验证。复杂度和派生格式检查属于资源准入，来源数值等价及重派生只在出口验证，不能移进每帧求解。定向回归通过不等于历史全库测试、GUI、游戏、安装或性能目标通过。

当前 CI 不再执行 race 双通道证据、pending pose 转交、向原 `fit_one` 注入 entry proposal、P3 `_offer_usable_fit` 分阶段发布、采样区域参数注入或默认观察域仅一次 residual 的历史断言；这些要求不能通过重新接入已撤下算法来满足。原 scanner 的取消／未知竞争者、原几何正反例、真实资源准入、显示 owner 和旧结果拒绝仍须运行。暖显示缓存检查禁止重复 NPZ／图片解码，允许实际资源当前性检查；不再以“禁止所有文件系统调用”替代当前读取作用域合同，也不据此声称零磁盘 I/O。

## 贴合速度与可靠显示的验收口径

首贴属于识别阶段，约 **500ms** 是目标；已锁图且首贴成功后的每一次重新开图，约 **300ms** 是目标。当前规范不宣称任一目标已经达成，也不改变验证门槛。

按同一单调时钟和开图轮次关联：输入接收、画面就绪、截图、楼层高亮判断、锁图后的求解、排队/验证与首次正确可见像素。先把首贴与后续开图分开，再区分算法计算时间和端到端时间；嵌套阶段不能相加，日志终点不是可见像素时不得冒称端到端。无真实按键时刻的回放写明起点是保存帧或事件注入。

连续样本报告版本/资源、模式/地图/楼层、样本数、p50/p95/max、未显示次数、错层次数以及超时判定窗口；百分位写明采用的方法。未显示的轮次不能从统计中无声删除，也不能记作零耗时。错层要对照有来源的图像真值；高亮尚不稳定、缺帧或无真值时单列未知。涉及楼层逻辑时按实际支持范围覆盖 B1、F1、F2 及过渡帧，不把所有模式都假定为三层。

前后对比注明是否同帧、同资源、同运行环境及热/冷缓存。保存帧回放可用于定位和回归，真实游戏的调度、截图就绪、绘图时延与长期 RSS/句柄仍须独立验收，不能从局部算法更快推导整体更稳。

## 合并后与安装包绑定

Windows 主程序的 PyInstaller `EXE` 必须显式指定 `uac_admin=True`。
仅在 XML 中声明 `requireAdministrator` 不够：PyInstaller 会按该参数覆盖执行级别。
主 spec 在收集前读取真实 PE 的 RT_MANIFEST #1，拒绝缺失或非
`requireAdministrator` / `uiAccess=false` 的成品；外部构建脚本必须保留该参数和检查，
并可对最终暂存主 EXE 再运行 `python tools/verify_admin_manifest.py <主程序.exe>`。
该检查只读取资源，不启动 EXE，也不证明 UAC 提示或快捷键已经实测。
安装快捷方式应直接指向主 EXE（工作目录为安装目录），安装完成启动应通过 Shell 执行主 EXE。
普通用户点击后由 Windows 请求 UAC，同意后启动，取消则停止；不得以计划任务或系统策略绕过确认。
导入 helper 使用 `asInvoker`，由主程序启动时继承其权限，不作为独立用户启动入口。

若快捷键仍无响应，最小可见诊断应同时展示显示版本/构建指纹、当前 EXE 入口和当前进程
TokenElevation 的“管理员 / 普通 / 未知”状态；查询失败必须显示未知，不能推定为权限不足。
本次仅修复打包合同，尚未加入该诊断界面；权限正确也不能单凭此断定快捷键故障原因。

合并改变了最终提交或调用关系时，在最终远端 commit 的干净 checkout 上运行受影响的联合检查，然后从同一 commit 构建。若某个未变模块的测试复用旧证据，记录对应文件/哈希和复用范围，不能将合并前的整体通过直接写成合并后通过。

交付回执记录 source commit、显示/数值版本、构建指纹、资源版本和锁文件哈希、实际内置范围、Setup 精确字节数与 SHA256。需要外部资源时明确获取与绑定合同；缺失时报告，不能静默退回旧本地源码/素材。软件只交付 Setup；新版结构包用 `.xingtu-base`，作者路线包用 `.xingtu-route`，内部容器仍为 ZIP。未生成安装器时不要用便携 ZIP 的大小估算安装包收益。

源码/离线检查、包内模块检查、安装、真实 GUI/游戏和线上发布分别标明结果。生成 Setup 不代表自动授权安装或发布；保留旧交付物和用户数据，未包含的 PR/修复不得写进当前版本公告。

## 发版列车

2026-11-11 起，正式版由中台（agent-autoloop）的发版列车按固定节奏发出，huan 已确认以下规则：

- **发版日**：每周一、三、五 10:00（北京时间）。改动在通过检查后的第一个发版日上车：周一开始的大功能，若周三前已合并且检查通过就赶周三，否则赶周五；没合并的改动不上车。
- **上车条件**：已合并进 `main`。发版日由列车从 `main` 最新提交加上新版本号开一个版本号 PR（只改 `version.json`，补丁号 +1，数字版本末位 +1），该 PR 提交上的 `joint` 检查（qt-next-integration-windows.yml）必须全部通过才打包；没通过就关闭该 PR，本次不发。
- **暂停**：任何未关闭的 issue/PR 带 `hold-release` 标签，或本次要带上的已合并 PR 带该标签，当天跳过。家里 Windows 电脑离线也跳过，并在飞书说明。上一版还在等验收时不另发。
- **打包**：在家里 Windows 电脑上，由「星图 打包」从版本号 PR 的同一提交干净构建正式 Setup，按本文件和 `experiments/qt_quick/PACKAGING.md` 的配方执行，成品放在 `E:\codex\releases\xingtu-<版本>-<提交前12位>\`，附交付回执与更新公告草稿。不安装、不运行 Setup、不覆盖用户安装目录、不操作游戏。
- **实机测试与验收**：目前没有能自动跑的实机测试；保存帧回放等离线测试不算实机测试。所以每一版都要等 huan 本机验收：打包后飞书发卡片，写明版本、包含的改动、本机文件夹、SHA256 和未测项。
- **发布**：huan 先自己把 Setup 上传到夸克和 UC 网盘，再在卡片上点「我验收通过，发布」。之后由本机 Codex 用本机环境变量里的发布口令运行 `tools/publish_windows_notice.py --publish --yes`（口令绝不打印、记录或复制），发布成功后中台核对线上版本，合并版本号 PR 并打 `release/vX.Y.Z` 标签——这次点击只授权这一版的合并。点「这次不发」则关闭版本号 PR，安装包文件夹保留。
- 以后若有 huan 认可的、能自动跑的实机测试，可以改为测试通过后自动发布；在那之前不自动发布。

## 通用机制优先复用

Qt 启动公告由窗口常驻组件承接，后台读取已校验的 Windows 清单，不依赖当前页面或阻塞启动。
公告身份是版本、versionCode、发布时间和纯文本正文的 SHA-256；同版本正文修订也属于新公告，
下载链接或大小变化不触发重复提醒。仅实际打开弹窗后记录该快照的已读身份，保留最近 64 条，
旧版仅含版本号的已读/跳过记录不足以证明读过同一正文，升级后允许重显一次。
隐藏、最小化、入门引导和关闭确认期间延后；显示中不替换正文，新结果在关闭后再展示。
偏好写入串行化并绑定显示快照，失败不冒称保存成功；退出后的网络结果不再显示或写入。
沿用 HTTPS 来源校验、纯文本显示和用户点击后才打开白名单网盘页，不新增遥测或个人信息上传。

Qt 迁移的默认启动链单独验收：单实例占锁须早于引擎/监听/worker；等待游戏时管理快捷键仍有效；严格身份的游戏窗口出现或重启后自动绑定，手动连接仅作兜底。应用内 `QShortcut` 不能充当后台全局输入。按 [Win32 低级键盘回调](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc) 保持独立消息循环和输入穿透，关闭时注销并等待所属线程结束；单实例参照 [QLocalServer](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalServer.html)，Windows 还需独占仲裁，不能只凭 `listen()` 判断唯一进程。

完整客户区采集传递“full-client”意图，由采集线程使用本次原生上下文计算完整矩形；UI发现窗口时的宽高只供显示，不得作为后续完整帧的固定ROI。保留原始完整帧、坐标、代次、取消和前台校验。采集错误须保留尺寸元数据与原始异常，向用户展示中文原因及可回看记录。退出还须覆盖错误发生后再退出、再启动；窗口拒绝关闭不能否决已排空的程序退出，IPC只有实际恢复窗口后才能回复成功，“退出中”或无法恢复必须明确反馈。

地图键与开始本局分别授权：局外 G/ESC 只由游戏处理，不准备对局、不采集、不识别或锁图，独立图库预览不撤销。主页、游戏控件或本局快捷键明确开始后，地图键才激活已准备的轮次；局内关图重开仍保留本局身份与定点参考。准备或结束正在提交时，地图键不抢占该动作，完成后重新开图。局外或准备中的键仍然穿透游戏并改变真实开关相位，拒绝本程序业务处理不能重置该相位；显式开始也不重配已启用的监听器。独立“重新识别”采集入口保留既有显式准备行为。结束后开箱恢复不再把局外地图键当作新对局意图，仍需已验证的新轮次及原有窗口、前台、释放按键等安全门。

默认入口需跟到开始本局 → 当前捕获/识别 → 当前图与楼层的定点/贴合像素 → 轻提示 → 关图撤销/结束 → 进程退出。图库独立预览不能复用不可交互定点窗口；定点与贴合显隐互不阻断。补测后台隐藏后恢复并退出、捕获任务进行中退出，以及重复启动唤回已有窗口；只验 idle 或刻意关闭自动绑定/输入的 UI probe，不能据此宣称整条真实使用链通过。对局中的游戏模式由业务层锁定，资源页浏览模式不属于该锁。

前端迁移沿用已验的用户旅程，不以服务函数可调用代替交互等价：

- 导入从真实文件选择器打开、选包、接受开始，普通新增自动校验导入，必要替换由所属窗口确认；核对进度、成功后的实际列表刷新、重复包反馈、取消和失败的最终消费者。直接调用导入服务不算文件选择入口验收；文件框未打开时调用 `accept()` 也不等于用户选包完成。
- 点 X 明确区分后台、退出和取消。后台可以保留进程；退出须有完整自有程序子进程正常结束的证据，并核对已接受写入排空、输入轮询停止和所属 worker/helper 结束。窗口隐藏、`quit` 信号或假 worker 小窗口均不能单独判完整程序退出通过。失败测试进程的强制清理单独记失败，不算修复证据。
- 页面导航保留选择、焦点和滚动；成功/取消/失败后不能由迟到结果还原旧页面。按钮、图标和图片需看普通/大窗口真实像素及鼠标/键盘状态。
- 在既有迁移映射逐项记录旧契约、Qt 实际入口、证据和未覆盖范围；区分实现、测试执行、独立复核，绑定实际 commit/包。源码截图、成品证据复核、独立端到端执行不得混称。

下载、取消重试、导入和退出清理等通用机制，先查官方教程及成熟开源实现，再结合现有服务接入；核对版本、许可证与完整状态生命周期。不要只照搬外观、引入无关整套依赖或顺带改写识别贴合算法。相关说明应简记实际参考及适配点，例如 Qt 的 [Window 关闭](https://doc.qt.io/qt-6/qml-qtquick-window.html#closing-signal)、[QCoreApplication 退出](https://doc.qt.io/qt-6/qcoreapplication.html#quit)和 [QThreadPool 生命周期](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html)。

## 本地数据占用上限

程序运行时在安装目录的 `work/` 下保存诊断和学习数据，各类都有上限，超出时删除最旧的文件；对应的 JSONL 记录保留，文件缺失按不可用样本处理：

| 位置 | 内容 | 上限 |
| --- | --- | --- |
| `work/nightmare-live/events-*.jsonl`、`startup-error-*.log` | 每次启动的事件日志（含 Qt 的 `events-qt-*`）、启动错误 | 各保留最近 8 份（与诊断导出读取的数量一致），启动时清理 |
| `work/nightmare-memory/failures/` | 识别失败截图 | 连同失败记录共 64 MB（`FailureSampling.max_disk_bytes`） |
| `work/nightmare-memory/runtime-successes/<地图>/<楼层>/` | 跨楼层成功截图（仅诊断） | 每层最近 6 张 |
| `work/nightmare-memory/samples/<地图>/<楼层>/` | 确认地图时的裁剪样本 | 识别实际使用的样本（每层最近 6 张可用的）加最近 12 张，识别结果不变 |
| `work/map-packages/downloads/` | 在线下载的地图包 | 启动时删除已安装（安装记录的压缩包 SHA-256 与文件名一致）的压缩包；有待恢复的升级、排队中的导入请求或导入锁时不删 |

用户主动接受、拒绝的贴合记录及其截图不做清理。图包升级或删除成功后，事务临时副本会完成清理；中断或清理失败时保留恢复记录并阻止新操作，不冒称成功。

直接导入时可只读识别内置认可清单中的完整旧版作者图；完整路径、大小和哈希均匹配、没有现有安装收据，且新包覆盖该旧版完整文件集合时，进入普通更新确认。确认并完成全包校验后才登记旧资源，再复用原升级事务；不依赖用户先刷新列表或手动找文件。残缺、改动、未知来源及其他包占用的文件不扩大认领。 更新也须保持其他已安装包 `requiresFiles` 的精确依赖约束；依赖不等于文件所有权，保持依赖字节不变时仍可更新其他作者图。检查和提交前均重验收据，损坏收据失败关闭。 普通安装收据必须保留完整基础依赖声明，缺失、空或损坏声明在导入和删除时均失败关闭；只有字段、文件清单及来源哈希与认可库存完全一致的旧领养收据可没有依赖字段。相同文件但依赖合同改变的重复导入目前明确拒绝，原收据和数据不变，不报告已应用或无需更新；这也包括旧领养记录与完整同字节 ZIP 之间的仅依赖迁移，实际图像内容改变的合法升级仍受支持。若将来支持仅迁移收据，须单独设计确认、回滚与 helper 合同。

Qt 客户端启动时做有界清理（`diagnostic_export.prune_diagnostic_logs` 与 `star_map_download.prune_installed_downloads`），Qt 的 `events-qt-*` 日志也在同一个上限内；Qt 有 `qt-import-pending.json` 或 `qt-recovery-pending.json` 时同样不删下载包。


### 默认失败证据（Qt，新自动样本）

新安装或没有旧设置项时，默认开启有限的本地自动失败画面。旧设置明确 `false`、非法值或读取失败均不自动开启。帮助页明确提示局部图仍可能含昵称或聊天，不承诺匿名。正常诊断导出默认附上与已导出日志会话匹配的自动局部图，可选择“仅导出文字日志”；完整游戏窗口的手动失败帧仍须单独开启，默认关闭，任何流程都不自动上传。

自动样本只复用当前合法采集，保存当前地图颜色区和检测器实际使用的右上控件带，不额外截桌面、不改变识别判断。保存失败也将有界 `failureSample` 状态和 `missingReason` 传到文字日志；没有合法帧时只记原因，不能补造截图。自动采样仍按失败类型和图类别冷却，完整手动帧仍有独立进程限额。

`failures/automatic-v2/` 是新的身份绑定缓存，每模式最多 6 张、12 MiB，超过 7 天或为新图腾空间时轮换最旧对象。新 PNG 的创建、写入和身份记录使用同一 Windows 独占句柄；读取或删除再次核对原文件 identity、单链接、尺寸及 SHA256。删除使用 `SetFileInformationByHandle(FileDispositionInfo)` 针对验证过的同一句柄，不使用 pathname unlink，也不回退到不安全删除。参考微软 [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) 的共享权限及 [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle) 的 DELETE 权限合同。

旧 `failures/automatic/` 缺少原文件 identity，只读保留与兼容导出，不为其补写当下 identity 后自动删除。迁移期间每模式可能额外留存旧版最多 12 MiB 样本；旧目录异常不阻断新 v2 空间。链接、外部替换、未知文件、未提交且仍有图片的写入、损坏 journal 都失败关闭并明确状态，不冒称完整恢复。删除完成但 journal 未更新的缺图记录可安全修复；部分 journal 写入或断电仍可能需要人工检查。POSIX 不实施自动图写入/轮换，Linux 单元测试的 Windows skips 不代表原生合同通过。


#### 同次失败事件关联与局部图覆盖

默认文字诊断仅记录已经过绑定与前台校验的本程序地图按键意图，不记录原始键码、其他程序输入或窗口身份。`inputSequence` 关联意图，`openingId` 关联初次准备及其有限恢复，`attemptId` 区分每个实际 worker 尝试；恢复不是新按键。准备记录区分采集异常、当前观测未就绪和超时，运行状态标为内存快照，不能把历史观测说成本次画面或把收到按键说成游戏已开图。

自动局部图只接受显式传入、属于同一尝试及相同 epoch/对局/赛季/模式/资源实例/采集代次的完整游戏客户区帧。开始新采集或失效时清除旧凭据，失败后不得回退上一帧；写入前后继续核当前窗口、前台与关闭选项。原几何失败及准备超时之外，当前身份不唯一、相似组待定、采集后的待确认、当前楼层不可用、恢复耗尽可产生证据决定。采集前待确认、正常关图和过渡重试不因此抓图；生命周期已撤销或没有可证明的局部区域时只记缺图原因。新增身份类失败只保留地图颜色区，楼层类失败只保留实际检测控件带；无控件范围时不得退回地图或整帧。地图颜色区仍未经过身份验证，局部图不保证匿名，不扩大到整窗/桌面兜底。

只有图片和最终 journal 都提交成功才消耗图片成功冷却；无帧/无 ROI 的元数据独立有界限流，不阻断同次控件局部图或下一次有效图；真实编码/存储错误保留失败退避。容量、7 天清理与会话匹配导出合同不变，有限缓存并不保证每次失败都有图，导出的 ZIP 不受本地缓存轮换自动删除。例行刷新不逐帧写采集成功日志；重复缺图状态按 60 秒合并并报告抑制数量，成功保存的样本事件仍及时记录。


### S1 private experimental package 1.0.13 (2026-11-05)

Includes the verified joint color-display/preferred-candidate base, common entrance coordinates, within-round B1-B4 positive-contradiction candidate accumulation, and a unique experimental lock only after current-frame material validation. A failed singleton fit restores competition. This is a private experiment, not formally calibrated identity. B3/B4 static screenshot evidence is distinct from pending real-time game acceptance. The compact UI shows experimental lock/current-map fit separately and explicit rediscovery resets the pool.

Cloud delta: 11 text files / 34 segments / 228035 bytes, SHA256 2bca682b87ceaff102c1b7d4b1d3a3dac00744c7b95843f13c1fa4391bb75281; all cloud after hashes and the locally derived pinned glyph verified before the Windows merge. Local four-floor qualification and installer identity are preserved; final runtime has its own hash. Six display-only JSONs match their target hashes; PNG/NPZ resources are unchanged. All 176 coordinate bindings match production typed metadata. Per user instruction no simulated recognition tests or accuracy matrix are a delivery gate; only integrity/build/startup/install/data-protection checks are run and recorded separately. No actual game acceptance is claimed.


### Private dual-lane first-material candidate 1.0.15 (2026-11-05)

Uses the verified cloud dual-lane delta rather than the internal synchronous 1.0.14 candidate. The unchanged current-frame fit/material path streams its first accepted candidate and cooperatively stops the main scan. One private evidence lane owns at most one running and one latest queued immutable verified frame. Ordinary map close revokes pixels while same-round/binding/resource/pool-revision guards permit historical evidence accumulation. Historical evidence cannot display or lock; a later opening must validate fresh geometry/material before an experimental lock. Only explicit same-coordinate contradictions remove candidates; unknowns do not. The B1 static resource cache key and bounded resumable static construction are fixed without changing downstream geometry.

Preserve the local B1-B4 qualification, compact UI, experimental labels, isolated installer identity and stronger map/frame/new-token material guard. New background stage logs bypass UI callbacks, preserve original attempt/opening/frame scope, and use the existing bounded disk writer. This baked private identity enables that writer without relying on an environment flag; other identities retain their existing default. Queue stop/admission cancellation and active-lane drain are wired to both normal close and partial-client cleanup, with no blocking UI join.

All nine transmitted file diffs plus the separately pinned helper were checked against exact cloud parent/source targets before local safety integration. Twelve narrow async ownership/queue/disk/purpose checks passed in 0.917 seconds. Initial default C:Temp disk check hit restricted-token WinError5; the owned task E:Temp check passed without changing storage safeguards. No accuracy matrix or game operation is an acceptance gate. Setup build and packaged startup evidence are separate. The user current 1.0.13 installation is preserved.

已核验旧作者导入收据的缺失依赖字段，只能按源码登记的原始发布 ZIP、包 ID、归档 SHA256 与完整拥有清单摘要精确匹配后，只读恢复原包全部 requiresFiles。不改写收据，不从安装目录推定依赖；未知或不匹配收据仍失败关闭。

启动阶段另按保留原则处理旧回执：缺少依赖字段但基本格式及路径合法时，仅把其文件声明作为禁止覆盖的约束，不恢复或猜测依赖，也不授予导入、删除或作者显示能力。格式合法的未知作者记录同样保留，不动态注册作者。此时基础资源修复可添加经过当前候选清单校验的缺失文件，不能替换任何已有基础字节；与旧声明冲突、坏记录、路径越界、无效的已有依赖字段及未完成事务继续明确阻断。严格图包操作与作者显示 provider 的独立校验不变；旧版基础资源确需替换时须另核其历史依赖与迁移范围，不能把“保留启动”当成已批准迁移或兼容所有旧资源。
