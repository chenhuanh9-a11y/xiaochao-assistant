# 星图完整 C# / WPF 迁移

当前是独立实验分支 `codex/full-csharp-wpf-20261109`，来自 PR #125 的 `29dd3f24cdaf46f79b9ce401ab5b0884b45194af`。Python/Qt 源码基线为 `7991dbbecdb66d76799415fb555d2c8a0361acc6`；PR #124 的计划提交为 `dc464ff2cf37deaa7cacc60c04639615e59d2def`。不替换正式版，不合并 main，不发布正式版本。

目标是包含原功能与完整 UI 的纯 C# 应用，依赖 Python 的中间状态不能作为完成。此前阶段一网格壳和旧 hybrid 测量均不是全量版本。

**当前真实画面要求：只使用 2026-11-05 及之后采集的原始画面。** 用户指出此前楼层控件位置已经改变。下面提到的 10 月原帧对照仅保留为历史数值移植证据，不代表当前楼层观察、识别或使用流程可用，也不再重跑旧图。新画面须核实原始采集记录和当前控件位置，不能把复制后的文件时间当采集时间。原结构资源兼容、纯状态与明确标注的合成数值测试单独记录。

## 实现顺序与验收矩阵

| 范围 | Python 真实入口/负责人 | 本轮 C# 状态与完成证据 |
|---|---|---|
| 启动/主窗口与持久页面 | tools/qt_client_entry.py → qt_quick/client.py / Client.qml | 实际 WPF 构造/持久页面回归通过；真实界面验收待完成，未接入页面有明确状态 |
| 资源图库、图包、按层结构 | client_providers.py / shared_structure_reference.py / client_resources.py | 只读 catalog/NPZ 已实施；15 个跨模式/楼层代表与 NumPy 回读一致，15 个类型/坐标/wall 反例及 20 个非 object JSON 反例通过；轮廓回退、解码预算、坏图/缺结构/文件占用恢复有实际入口回归；图包安装/版本锁与完整 provider 待接入 |
| 设置与自定义路线 | client_settings.py / client_custom_gallery.py / client_paths.py | 实验副本、批量/单字段保存与原件保护已接；坏尾项显式解绑、未选中坏原值保留、其他草稿隔离、忙写/冲突有真实文件回归；实际 WPF 保存期间编辑及导入编辑禁用/恢复通过；图库编辑与运行消费待完成 |
| 捕获、窗口绑定与输入 | client_capture.py / client_native_keys.py / client_input.py | 11/09 生产 GDI 完整客户区实采 1334×750；WGC 三轮软件呈现自有窗口及取消/关闭 12 项通过，默认硬件呈现同窗口仍白/黑帧失败，未证明游戏 WGC；pass-through 模块 408 项和 WPF 主机 46 项离线检查通过，早期原生启动/停止通过；WPF 已接显式监听、设置切代、本局操作和默认 F11/主页显隐，构造不监听。当前组合尚未完成真实按键/游戏验收 |
| 楼层/开图准备 | client_live_runtime.py / map_open_preparation.py / floor_contract.py | 37 项真实/合成边界及全部 S0 251 张原始客户区的观察结果与原 Python 严格一致；S1 当前真实输入由全 154 张组件回放及连续 Runtime 样本覆盖。map/foreground-return 准备沿原 1.05s/50ms/最多 21 probe；普通 capture_now 单次观察。超时未知楼层的正地图帧仍供人工修正，晚到可用楼层不绕过期限；有原 Runtime 真实晚采集对照及 C# 修前失败/修后回归。不是实机准备延迟验收 |
| 身份与入口竞争 | map_identity_engine.py / difficult_entry_identity.py / difficult_side_entry_identity.py / front_door_recognition.py | S0 完整 passport/main/side/source-advisory/native/登记组/最终行消费者已接，全部 251 张当前原图严格零差异；S1 正支持候选始终不充当唯一身份。组内成员选择已接独立目标贴合、WPF 素材回执与同锁提交，手选跨层保留。实际调用已按原 Runtime 纠正：有选中参考时不额外重排；无地图且有实际条件先验时以完整 cohort 竞争。强冲突观测及配置 source store 时的 SIFT producer 仍缺 |
| prepare/grid/refine/propose/验证 | fast_structure_alignment.py / single_map_structure.py / nightmare_candidate.py / idvb0059_steady.py | 共用数值内核与 S0 Host 冷流程/拒绝顺序、S1 目标楼层 Fit 已接；S0 当前 285 次候选 Fit 与原程序零差异，另有禁用 fast 的 6 次真实原 Host 对照。0059 数值流程和 coverage refresh、S0 established 几何组局部/全局/native recall 与 entry producer 已接；S1 原 explicit-full-frame 路径继续跳过0059及 established 几何恢复。准备/观察数组有逐位对照，标量有严格差异报告。pose-index、tracking 与强冲突 producer 尚缺 |
| 会话/取消/缓存/恢复 | client_session.py / client_coordinator.py / client_live_runtime.py | SessionService 单写者/单 worker、取消、资源 lease、真实回调重入及有界开图恢复已接。Core 几何组/目标层尺度与素材 ACK 的 owner 分离；S1 不请求0059学习。隐藏页面 timer 经 Session 执行 S0 刷新与 S1 专用楼层观察，同姿态续期重新测当前帧残差。人工确认先发布身份，开图周期中同操作重采集，失败保留选择；确认关图同步原 G 相位。S1 独立手动准备/当前画面贴合及 S0 人工标签持久化已接；点击意图、保留观测的组内拟合仍待接。以下分节记录实际接线与证据范围；Recorded 始终不授予 LiveAuthorization |
| 结构/作者/浮窗/穿透/DPI | client_windows.py / ClientAligned.qml / ClientReference.qml / author_display_policy.py / nightmare_author_coordinate_adapter.py | 显示桥当前 391 adapters/391 bridges/5 native corners/1 非单位校准零差异，完整选择 233 项和素材 261 项通过。Runtime 已接 canonical/material 提交、偏好替换与撤销；浮层已有真实 WPF 主机和离屏几何/票据回归。完整作者交互、参考窗及实机穿透/多屏 DPI 仍缺；素材不授权身份或结构位姿 |
| 账号/路线广场/投稿 | account_service.py / client_route_plaza.py / ClientAccount.qml | 待迁；不以演示数据或按钮提示冒称接入 |
| 教程/公告/帮助/诊断/更新 | client_info.py / update_service.py / ClientHelp.qml 等 | 待迁；关闭/后台恢复、状态与错误路径需覆盖 |
| 自包含 Setup/数据迁移/升级回退 | 当前 Python 安装更新流程 | 待交付完整实验 Setup；干净 Windows/中断/回退仍缺可恢复环境 |

状态随实现与证据更新，不因页面存在就改为功能完成。每次交付记录源提交、资源来源、真实输入、构建、GUI 与未测项。Python 原件、玩家数据和历史失败证据保持可恢复。

### S1 门坐标索引与候选提前贴合

逐帧候选扫描不等于原 S1 的累计排除。现已接入门图标定位、预制入口坐标索引及共同结构的正向冲突比较：本局首次可靠定位门图标后，后续跨楼层、无门画面继续索引共筛。门尚未定位时走普通结构拟合。普通拟合失败、未知楼层、预算耗尽和坐标未决均不能淘汰地图；整池冲突保留上一候选范围并报告冲突。只有新对局创建全候选池，关图、重选和换层均不能恢复已排除项。通用候选池与比较内核分开，当前生产索引适配器使用 S1 资源，尚未声称 S0 已建立相同索引。

候选未唯一时可提前提交首张通过真实当前帧残差和 Core 的临时候选，`ProvisionalMapId` 与正式 `Context.MapId` 分开。后续只在剩余范围内优先验证该参考；被有效排除后替换，当前层无贴合时撤销像素而保留身份候选。自动锁图要求索引只剩一张且该帧真实素材已提交；显式手选仍立即锁定身份。楼层结构、描述子等不可变资源准备结果按实际资源 owner 复用，不借用其他楼层的尺度或当前帧变换。

当前原图对照包含 S1 困难全部 62 张（55 张完整候选扫描、7 张闭图或楼层未决），107 条实际 Python 索引报告与 C# 对照，以及完整 Runtime 的 14→3→1 递进。旧版在 261 的整池冲突恢复 14 张；按用户新规则保留 3 张后，307 能继续排除到 106008。它是算法结果，不把用户此前“分不出”改写为独立身份真值。预算定向反例另证明 27,264 个可行假设不可被误作无解；修正后整个观察返回未知，而非用不完整域作永久排除。

以上仍是录制客户区、实际算法与 WPF 素材消费的验证，不是原生游戏/显示延迟验收。预算反例执行的是实际 domain 单元；整帧预算异常的上浮另由源码消费链核对，尚无真实帧触发整帧预算的集成反例。多楼层共同场景的状态如下；后台并行复核仍缺。CartoVeil 的机制研究另存外部报告，不将其静态实现或语言选择称作星图性能收益。

### 同姿态续权与稳定素材

S0 默认尚无已记忆0059素材、或公开关闭0059时，`refresh_map` 会先对原生矩阵执行真实新帧 canonical residual。通过后保留图片、token、几何组与历史 pose，仅提交新的 Core observation/authorization；不以旧授权冒充新帧，不跨楼层借尺度。原 Python 五条公有路径（噩梦、困难、已记忆后关闭0059、作者意图重投影、换层拒绝）供独立事后比较，未将答案矩阵注入求解。作者案例实际资源缺艺术层，仍是 shared-structure fallback。

素材就绪、当前帧记忆 proposal 的 accepted/consumed、0059 prior 是否可写分别维护。迟到的同素材回执和排队任务只能绑定最新的已验证 proposal；关闭、换层、换作者素材或本局/资源失效后拒绝旧回执。关闭0059允许消费就绪任务，但不写新 prior，也不清既有 ready cache。

`PreparedDisplayPixels` 与本帧 `PreparedDisplayLayer.Bridge` 分开。资源 owner 的 producer 按楼层数保留最多两套地图的像素缓存，核对实际来源、资源快照、静态桥、neutral mask 和 atlas 图标布局；每次仍检查实际文件及本帧拟合参考。新位置/尺度保留 payload，但生成新的 bridge/material/proposal。WPF 按像素或共同场景 owner 复用 frozen BitmapSource，并给每帧新 surface ticket。运行时 affine 直接设置 Image 的 RenderTransform、opacity；Canvas 保留原图完整范围，客户区在变换后裁剪。CPU/projective `Prepare` 仍可用，不把完整 H 截成 affine；没有 GPU 上传次数或实机端到端提速结论。

### 多楼层共同场景

`CommonFloorSpace` 从实际原生 NPZ 声明读取静态 reference→common 坐标。S0 使用 renderer bounds、60px 静态网格和 product 变换；S1 使用 imageToMapPlaneXZ。它们是资源位置，不是目标楼层测得的 scale seed 或可信 pose；已有校准桥按声明合成。252 层实际 NPZ 均已检查，旧困难图29两层缺少可靠共同空间，继续独立贴合，不补造关系。

`RuntimeSceneSources` 准备同图各层的真实显示来源和各自静态桥，最多保留两套地图/偏好。当前帧仍通过原数值拟合、残差和 Core canonical 提交，由唯一 Runtime owner 派生共同根变换；未将静态关系写进几何组、0059 或其他楼层求解先验。WPF 同时持有各层 Image/source/transform，仅显示当前获许可的层；共同根变动会一起更新隐藏层。关图撤销 ticket 和显示许可，保留不可变素材；退出释放视觉引用。缺失层逐项报告，不能获得显示或学习许可。

`phase15-workspace-scene-04-four-modes` 使用四模式真实录制输入，检查所有楼层常驻、完整客户区像素与既有显示桥一致、Image/source/transform 实例复用，以及 C1 的319→323真实换层。后续同组检查覆盖96/120/144/192 DPI的软件像素。隐藏层坏资源曾导致每次创建新的partial scene，`phase15-workspace-scene-05-partial-before` 保留实际失败；修后继续重试缺失层，但可用来源、静态位置和缺失原因均未变化时复用同一owner，资源修复后再替换。未观察楼层的显隐/共同移动是明确的离屏显示控制，不是游戏拟合成功。常驻准备增加的首次开销、真实游戏切层、GPU 上传和首像素延迟仍未测得；500/300ms目标未据此宣布达到。

### S1 同帧结构准备

一次 `ScanStructure` 用 `StructureFramePreparation` 独占一个BGR副本，索引与候选扫描串行借用完整原始区域、按实际区域身份缓存的观测和惰性轮廓。Primary仍选首个最大原始区域；索引的链数/32区域过滤不影响Primary。扫描自己的bounds缓存、marker裁剪观测、每个候选的拟合与残差拒绝继续保留。普通公开Mat扫描入口仍防御性复制；共享owner只在本次操作内存活，取消/异常/返回统一释放，未加入跨帧或跨楼层求解先验。

共享路径与原Python全部154张S1原图对照通过：129张完整扫描、25张开图或楼层未决；107条原索引报告及原预算反例通过。实际Runtime全62张困难序列仍是359项通过。四种主组件选择控制与原路径逐像素/逐链对照，六张原图的成对索引/完整catalog扫描比较保留全部非计时字段；未注入预期地图或矩阵。配对耗时不是生产首材质早停、WPF或游戏延迟；没有500/300ms结论。

索引拒绝的低链观测可保留到该次操作结束，最多沿用现有32区域准备范围；复用不表示峰值内存降低，RSS/原生句柄尚未测量。诊断的ComponentCacheEntries含null缓存，ChainCacheEntries表示已有链缓存的组件数。后台复核/算法并行尚未由这次串行共享实现。

### 游戏内楼层点击

S0 游戏地图上已验证的完整楼层控件已接 Raw Input → 不可变观察快照 → WPF 准入 → Runtime 点击意图 → Session 新截图。命中只撤销旧贴合并请求复核；2 秒点击意图有效期内须新帧高亮一致才继续贴合，过期后按当前观察处理。原按键/鼠标仍传给游戏。单一原子槽由 Runtime 发布无像素元数据，原生线程只读；观察前进不撤销排队中的有效按下事件，严格拖动通道仍要求原帧一致。

原版当前截图的 20 组受控操作与 19 个控件边界覆盖连续点击、被动观察前进、2 秒期限、DPI 变化、已有手动保护及捕获抛错/空帧。手动保护跟随新目标但保留原截止时间，游戏点击不创建新保护；当前捕获失败撤销意图及贴合，保留真实开图状态。旧 Session 请求在 Runtime 入口被退休，旧取消回调不能撤销新意图。实际 Session 延迟入口/忙捕获、WPF 有界通道与 Dispatcher 排队均有定向回放。捕获、前台与原生事件源由明确替身提供，未进行真实游戏鼠标验收；一次未完成的 WPF 测试已保留，不能用后续通过消去。

S1 原观察器仍不提供已验证控件几何，因此物理点层不授权；实际 S1 观察拒绝、关图及新局边界单独验证。地图关闭点击见下节；拖动和完整失焦/返回计划仍待迁移，本节不构成首帧上屏延迟证据。

### 游戏地图关闭点击

沿原版先验当前指针上下文、先判断楼层控件、再判断唯一可见 X。搜索带、ESC 标签和导航圆圈本身不构成关闭证据；完整截图尺寸须与客户区匹配，不做 DPI 猜测或裁剪重缩放。X 在现有 BGR 观察中准备一次，仅保存不可变边界，按下事件不额外截图、解码或调用原生业务回调。全部 405 张当前原图和 5 张合成边界图的 X 与命中结果同原 Python 严格一致。

真实 WPF 通道经 Workspace/RuntimePage 接入即时撤销 → 取消旧工作 → 队列 close_map，并重置 G 的开关相位。原版协调器关闭/导航/G 重开测试已执行；四种模式的原资源/录制画面控制序列分别对照开图、立即关闭和重开状态。S1 显式地图是模拟人工选择，不能当作自动识别真值；关图保留本局身份和候选池，S1 手动模式立即撤销当前楼层观察。旧点击不得关闭重开的地图，旧工作不得恢复已撤销的画面。

原始控件观察与可用于贴合/学习的数值帧独立：人工确认和忙碌采集可保留 X 上下文，同时将数值帧置空。当前 acquisition 的空帧/抛错撤销位姿、推进所拥有的输入代际并保留真实开图状态及原始控件；新代际可关闭，旧点击拒绝。采集失败与新观察提交后的贴合异常分开处理，避免丢弃合法独立人工标签。已用保留旧 current stamp 的模拟采集忙碌反例验证旧图不能进入人工学习；当前失败回执及学习诊断按实际请求所有权消费，外部撤销不能借相同可见字段重新获得归属。

这些是原版对照、录制帧、原生源/前台替身及软件 WPF 调用链验证。未使用真实鼠标/游戏、未验收可见像素或端到端速度；准备预算在首次 probe 前耗尽的精确 raw-context 端点仍缺直接原版执行证据。拖动、完整失焦/返回及后续稳态恢复仍待迁移。

### 人工确认与新截图

`confirm_route` 的显式地图选择和无 payload 候选确认共用异步 owner：先锁定并发布身份、撤销旧姿态，再在已有开图周期时调用现有捕获→观察→目标层拟合→canonical/material 链。开始新采集前清除旧 `_frame`，避免 Session 把捕获端自身更新 stamp 误判为外部上下文失效；已有开图意图保留，不拿旧像素续期。普通会话闭图时仍只锁身份、等待后续开图。入口楼层种子读取实际 pack 的 defaultEntryFloor（沿用原 legacy 缺字段规则），只作显示意图，不能授予观测/姿态。

采集失败或异常保留已选身份及已有开图周期，并撤销像素；新截图未观察到地图才结束该周期。未知楼层不能继承种子来拟合；取消、资源或窗口失效、关闭后的迟到结果仍被现有 epoch/绑定/帧校验拒绝。确认结果参与原 G 输入相位回读，不生成按键。原 Python 的19组真实截图/资源/solver 操作覆盖四上下文、跨层/视口变化、未知/负帧、采集异常及闭图 pending；仅替换 OS 捕获与 memory 路径，S1 native-preview 组合搭配录制 fixture 不代表实机。

无 payload 确认在当前楼层未知但开图周期仍有效时也会重新采集：这是相对原入口缺少楼层即拒绝的明确扩展，使候选确认遵守身份立即锁定、随后重试对齐的要求。普通模式的两秒手动楼层保持不被地图确认清除；新截图高亮与保持目标矛盾时，在索引/拟合前拒绝 `current-frame-floor-conflict`，不把错误楼层写入候选池或姿态。

S1 的 `prepare_manual_alignment` 建立独立会话标志，初始不捕获、不填地图或楼层；`fit_manual_frame` 只对已选地图重新采集。专用会话的首次确认即使没有旧帧也采集；关闭保留模式与身份，显式贴合可再次开图。普通会话中的手选身份不改变会话模式或楼层保持策略。结束本局/资源或模式切换清除此标志，重选地图/换作者保持它。G 开图在该会话有身份时调度 `fit_manual_frame`，仍沿用输入序号、准备/恢复与迟到隔离；关图不捕获。

确认与专用手动会话共用采集失败收束：非取消异常撤销姿态/素材；专用会话还清当前楼层和帧，但保留已经观察到的开图周期与身份，下一次被动观察可以重试。生产捕获端把部分错误转为 null-frame，专用会话的这种失败也进入同一收束。明确负帧、关闭输入与外部上下文失效仍关闭周期。原 Python 两种 S1 模式各自的显式贴合/被动观察异常及随后恢复均已用真实 PNG/资源执行；C# 定向回归分别保留修前异常停止后台观察、null-frame保留旧楼层的失败结果。S0 人工标签的独立收束见下节，不由几何成功授予。

工作台验证经真实资源列表的 SetMaps、非激活的屏幕外 WPF 窗口、页面内容加载、准备手动贴合/地图选择/贴合当前画面按钮事件、Session 和 Runtime 消费。页面隐藏时仍按原规则不更新内容；按钮与 G 入口由程序路由，未发送原生鼠标/键盘，也不代表可见像素或游戏验收。

### S0 人工标签与原存储兼容

原 Qt 生产 provider 的 S0 `MapIdentityEngine` / `NativeDifficultCandidateEngine` 不读取人工 exemplar 来排名，`preload_memory` 明确无操作；样本匹配与 memoryBoost 属于旧基类路径。当前迁移保留真实的人工标签/JPEG 写入、读取与刷新接口，不把保存成功解释为识别自适应，也不把独立 source-feature 索引混成同一机制。

只有显式确认进入标签提交；S1、未注册捕获来源和 Recorded/Synthetic 均跳过，action payload 不能提升证据等级。生产捕获端固定声明 LiveClient，并仍要求实际帧同级、当前绑定/stamp、epoch、资源、模式、本局、地图、目标楼层和开图状态均相符。新采集失败不借旧帧保存；贴合拒绝而当前人工确认上下文仍有效时可保存。接受后保留不可变客户区像素和物理屏幕 bounds，在状态锁外写入；随后关闭不撤销已接受的写入，也不能由旧写入恢复场景或新局状态。

WPF 主机将已有 `ExperimentalDataRoot` 显式传入，存储为 `work/<mode>-memory/confirmations.jsonl` 与 `samples/<map>/<floor>/<JPEG-SHA256前20位>.jpg`，不发现或修改正式 Python 数据。沿原宽屏裁剪及 JPEG quality92、完整 JPEG 摘要、labelSource/state/runtimeDisplayEnabled/sampleEvidenceAccepted/clientBounds 等字段；摘要用于原文件格式与样本去重，未增加提交 gate。写入与刷新错误分别保留 saved/reloaded 状态，WPF 独立显示标签错误并保留贴合，自动开图不重试人工写入。

兼容 reader 保留 append-only 标签；相同 JPEG/标签取最后一条，冲突地图/楼层标签的整组摘要不作 exemplar。每地图楼层保留最多六张可用 exemplar 及磁盘最新十二张样本的并集；清理候选需有原 JSONL 的精确路径和完整摘要声明，删除前用已有摘要复核当前内容，保留未知/被修改文件。缓存不在 Git 中，文件名/主键也不能证明内容未被替换，此处沿用既有摘要处理具体删除边界，不建立新摘要格式或冻结 gate。路径必须位于显式根内，所有祖先至盘根均不经过符号链接。原先没有 evidence 标记的记录按原模式地图观察重新校验，已明确拒绝的截图不重新提升。读取这些记录仍不授予身份、姿态、显示或 0059 权限。

原 Python 的13组实际资源/solver 操作覆盖两种 S0 模式、1F/2F/B1、保存、Recorded、闭图、采集失败及错误地图拟合拒绝；地图标签是显式模拟人工输入，不是唯一图像真值。测试捕获端只在隔离目录显式模拟 LiveClient 来源，不是实机。额外回归覆盖写入前失效、接受后关闭、真实文件锁及刷新失败、去重/冲突/保留和实际 WPF 选择按钮→Session→存储→错误文案。无换行的截断尾保留原字节并补行边界；标签 Flush 后的整理错误独立报告 Saved=true/retention，不能倒推为未保存。四组真实文件恢复/未知内容/父目录 junction 反例保留修前失败和修后结果。最终通过项以对应提交的验证回执为准。

### S1 专用被动楼层观察

隐藏页面的既有 timer 按原 0.5 秒间隔，经 Session 调度 `observe_candidate_floor` / `observe_manual_floor`。普通会话同层探测保留原 packet/帧/图片，不借新截图续期旧姿态；变层或无可显示姿态时再采一次并走原求解。独立手动贴合会话的观察用一次当前截图，未知楼层撤销姿态但保留地图身份；有效高亮立即清理旧手动楼层，不套用普通会话的两秒 hold。显式负帧立即关图，并同步既有 G 输入相位。

手选地图同层复用现有 Core 续期提交，S1 以完整客户区 `Geometry.Current` 和对应参考 validation mask 复核原矩阵，保留源图片；失败回到真实目标层独立贴合。该路径不进入 S0 几何组/尺度学习，也不改变跨层候选池。候选探测、手选观察和 S0 刷新共用 worker 的非排队合并；取消、过期输入、窗口/资源失效和迟到结果仍由 Runtime owner 校验。

原 Python 专用操作已使用相同录制 PNG、真实 detector/资源/solver 重算；仅替换 OS 捕获及 memory 写入位置。候选 oracle 使用原 native-preview 组合契约搭配录制捕获替身，不代表实机。原版无累计池的稀疏层临时候选与当前共筛结果可能不同，矩阵只比较同一帧、同一地图资源独立求解的结果。未知 event400、闭图和目标层323均进入真实消费者。S1 两分支的 `controlGeometryVerified=False` 保留，未据此虚构可点击控件几何；一般点击意图迁移、自然 -4 层与实机观察仍待完成。

### S1 索引投影复用

多候选距离比较按512个假设准备公共像素offset，再读各候选自己的距离场；每张地图每个点仍按原假设顺序做double Math.Min。保持完整候选/点/假设、除法、ToEven、边界外固定10000和严格距离>8的反证规则。单候选走直接循环，避免无复用收益的offset缓冲；多候选临时offset至多约1MiB，不跨调用缓存。取消仅返回obsolete/UNKNOWN，私有部分minima不写入候选池；domain预算及整池冲突策略未改。distanceComparisonMs单列距离核及minima分配，其他阶段仍单独解释。

实际私有核的56项检查包含逐位minima、独立字面值、批尾、阈值边界与取消；原107报告/真实62张连续Runtime另由实际入口验证。保留旧3d7efdd编译产物后，对相同7个原始输入组合各重复4次，交换进程执行次序，56对完整公共Evaluate报告的非耗时字段一致；未决、整池冲突和14/3候选输入均保留。

该Debug离线公共入口中，event257的14候选均值约654→348ms，event307的3候选约1176→619ms（每组8对）。未执行距离比较的未决输入基本不变；不是总体成功率、capture/材质/首像素或游戏延迟，不能推导500/300ms目标已达到。单图合成核经移除缓冲和内联原ToEven算式后与原参考接近；没有单图真实公共入口计时或Release/GPU/长期内存结论。

原 Qt 的 NoShow RenderControl 软件输出另有41例（37个控制、两张自然求解素材各有 recorded 拒绝与绘图副本）。纯显示副本显式替换授权布尔；原 Runtime 输出和拒绝控制未改，不是实机 Live 验收。四档 DPI 的坐标、独立整数控制已核对；一般采样并非逐像素一致：Qt 软件纯平移会将绘图目标取整，WPF 保留亚像素采样；剪切恰落边界时存在覆盖归属差异。两张真实素材 Qt/WPF 最大通道差为2/4，实际角点差小于0.00005逻辑像素。保留原 H，不作半像素或整数补偿。该软件证据不代表原 GPU、实际显示观感或耗时验收。

## 技术与视觉选择

新 WPF 产品采用 .NET 10（SDK 10.0.403）与用户指定的 Material Design in XAML Toolkit，精确引用 MaterialDesignThemes 5.4.0。布局先恢复原 Qt 的页面职责和常用操作关系，再应用主题。Core 不依赖 WPF 或 Windows App SDK。初期自包含、不裁剪；具体体积以完整 Setup 测量为准。

已从实际原 Qt 入口和 QML/services 取得八页离屏渲染，使用全新隔离配置与资源、禁用原生输入和采集；这是源码界面证据，不是已安装版本的桌面验收。Material 5.4.0 官方项目明确包含 net10.0-windows；toolkit、Colors、Behaviors 与字体声明随输出携带。当前源码已调整首页、导航和设置分组，新的包锁、裸构建、可见界面、键盘焦点与性能仍须分别验证。

来源：[Material Design 5.4.0](https://github.com/MaterialDesignInXAML/MaterialDesignInXamlToolkit/releases/tag/v5.4.0)、[目标框架](https://github.com/MaterialDesignInXAML/MaterialDesignInXamlToolkit/blob/v5.4.0/src/MaterialDesignThemes.Wpf/MaterialDesignThemes.Wpf.csproj)、[主题配置](https://github.com/MaterialDesignInXAML/MaterialDesignInXamlToolkit/wiki/Getting-Started)。

## 数据和同步

完整客户端入口是 `tools/qt_client_entry.py`，不是 `qt_quick/main.py` 图库实验。原结构资源优先兼容现有 index/schema/NPZ 与每层坐标；实验旧资源来源不等于当前 main 锁已验证。绝不改资源、抬阈值或伪造缺项来凑通过。

本机与云端以明确 Git 提交/分支交接，按模块单写者集成；不强推、重置、覆盖未交接修改。实验保存点 `7b44066859a66bb38660a6113aa82d567fb8c6bb`、`274762fcc8acba1907bf6dcc3248a44ee2fe50dd` 及输入/侧门结构 fallback/静态显示桥保存点 `6a541090b693ab3de47006200b85b14fa955aced` 已推送并回读远端分支 SHA。后续完整模块仍在集成；云端源码工作区未直接查证。

旧 hybrid 预定小时测试因新全量目标正常停止：实际 679.983121 秒、136 请求、1 次暖机接受差异，完成标记 false；无已观测自有残留。保留原始数据与差异，不称一小时通过，也不将它当作新纯 C# 版本测试。

## 当前证据与限制

- 实际身份入口纠正：原 LiveRuntime public dispatch 的6情景、19次 capture、22次真实 Host fit 区分自动参考、手选 pending、ACK后held和reset。自动首帧自行选04，后续保持04；此前人工12参考的换层成功不能套用。C# 曾在下一张 event3 重排并丢失地图，`runtime-identity-entry-before-01` 321/322先复现；移除额外 `group-default` 重排后，实际调用及手选/reset消费者回放 `runtime-identity-transactions-after-03` 512/512。`workspace-identity-entry-11` 又检出旧断言要求失败换层清空成员表，这与上述原实际入口相反；现改为严格核对保留04/12/19登记成员、参考04、目标12未持有及无本帧素材，并在断言前保存实际WPF状态。原组字段只是兼容参考，仍非唯一地图真值。
- 实际先验入口：原 Runtime 的7→8→7自行产生3域6个绑定，后两次真实调用 review，候选26→3→3；三帧都未确认身份/几何。C# before04仍扫26张，519/520失败；改接 `RankEstablished` 后，after-bind05进一步检出重复像素被捕获编号重复计数。按原 `stage_similarity_rank` 恢复 BGR内容摘要作为观察ID（捕获新鲜度仍用原独立帧号），after-content06为15/15，重复观察数1→2→2。这是原证据去重语义，不新增持久 baseline 或授权门禁。
- 条件重审组件：真实目录派生先验的21个独立组件输入中，5个执行真实 cohort rank，16个非法/零预算返回空 rows，绝不回退全局扫描；人工先验不冒称 Runtime 已建立状态。组件21/21全字段对照通过，非计时标量无差异；仅按已有规则将 Python set 派生的8条条件观察及其合计380条 pair 按显式唯一ID排序，保留原 rows并检查重复ID，其他字段对称核对。初版18/21的另两失败来自 SideEntryRanker 早退仍写入未执行分支的错误字段；已修正实际 Finish 写者，正常搜索错误保留。困难正向先验延续、真实 incomplete/singleton 库存仍缺，未造资源凑覆盖。
- 未选图楼层控制：原实测S0困难event99的2F不调用rank/review/Host，C#恢复该入口门槛；已有地图的2F拟合仍独立执行。原S1困难event265为-2层，继续候选扫描；C#恢复候选存在时的成功回执及原reason/source，同时保持无身份/位姿/上屏授权。before07/after-s0-08分别保留0/1、4/5失败，after-s1-09为8/8；不把“候选就绪”算成地图锁定。
- S0 established 数值对照：在全体249个非 seed S0原图情景中，原 Host 默认实际调用恢复21次，均拒绝；C# 数值内核与完整 Host 的21次结论/中间诊断对齐，45,259个标量仅1处约7.11e-15微差。另以原支持的 fast/0059 禁用配置得到7次恢复、4接受3拒绝，5,050标量最大差约2.84e-13；这些是配置隔离实验，不是默认运行成功率。各情景独立从本轮自身 cold seed 求解，oracle矩阵仅用于事后对照，未注入求解。真实资源仅覆盖无 known/unknown 的1F，尚未证明 scale-proof、pose-index、已登记跨层成功或其他资源通道。
- 实际组生命周期：原 Host→原 group writer→原 Core→素材 ACK seam 的三个全启用情景共10步，与 C# 实际 Runtime 的组根、verified/relation floors、owner及 exact coverage 对齐；成功无关系换层会换根并在 ACK 后只留当前层 row，失败换层保留原组。21张默认恢复帧另经 Runtime 当前0059 miss/真实恢复调用，确认拒绝不留本帧位姿/素材且不撤销手选身份。A1仍只是相似组内人工参考，不作为唯一身份真值。WPF 204项现有检查通过，但不代表可见窗口/GPU/实机验收。
- 跨层静态策略：24个原 adapter案例（2个真实资源无关系、22个人工策略边界）已对齐；人工关系不写入真实资源。独立审查发现 C# 图 writer 漏掉原退化矩阵过滤，定向 graph 入口先复现奇异边误登记，再恢复原 pivoted LU/log-determinant 的 `abs(det(linear)) >= 1e-8` 判断，包括截止边界。外部初版 policy oracle 共享可变字典曾改写早先案例输入，已改为逐案复制并回读原调用文档；失败证据保留，不归因于产品实现。
- 0059 组件数值：`steady-csharp-intermediates-02` 与 `steady-csharp-s1d-04` 共5个case定义、10个 caseRun、28个分列 step，26接受、2拒绝（319的两种clock）。四模式参考都只证明该数值组件；S1 直接调用实验不代表原 Runtime 应采用0059。162对 NPY 载荷逐位相同；合并严格标量对照每种 clock 各4,171项，3,787项位相同、384项有微差，最大绝对差约4.09e-12。confidence、coverage及投票门限位相同，最终scale最大差约1.55e-15（14 ULP）、平移约1.48e-12；S1困难自身cold种子已存在该scale微差，不注入oracle种子消差。真实 precision 保留10ms截止，numeric-zero只在内部测试 seam 固定该精修时钟。
- 0059 运行接缝的纠正：`49f2b5b` 的160/160及111/111没有检出 S1 调用范围错误。复核原 `_solve_observation`→`_AlignmentHost.fit` 发现 S1 明确传 full-frame bounds 并跳过0059；`s1-explicit-host-original-06` 用真实 Host/原图/资源及 real adapter 外层计数器验证13次 S1 调用为0、S0 implicit调用为1。`workspace-s1-explicit-before-05` 先复现素材就绪后S1误走稳态。现按原入口移除 S1 捷径与学习，S0 仍只在实际 WPF 素材回执后提交目标层尺度/coverage。`workspace-s1-explicit-after-09-configure` 204/204项混合检查，包括 S0 无ACK/隐藏ACK/跨层返回/新局/迟到/重绑/禁用，以及 S1逐帧原Host结论、矩阵、拒绝后无旧图、零稳态调用和零陈旧Session结果。`idvb0059Enabled` 导入布尔值及环境开关受支持，设置沿用结束本局后应用；可视编辑器仍缺。
- S1困难 `106008` 是数值参考，用户对 B1～B5 回答“分不出”，没有唯一身份真值；255的五候选不自动锁图。原 full-frame Host 对254/259/260拒绝，现 C# 保留相同拒绝和人工参考，不能用之前组件稳态接受覆盖这些事实。修正回归06首先在组合状态检查失败；07诊断因IntPtr不能JSON序列化失败；08独立回读确认是测试直接 dispatch set_context 绕过设置重配导致1次ContextMismatch，09改用实际 Configure/Session.Reconfigure入口后为0，未放宽陈旧结果断言。早期02向旧人工确认用例误传自动登记组帧的失败也保留，03恢复该用例原11/09帧。运行中取消、coverage pending失败续接、LRU/资源/几何DPI交叉、作者错误和实际 HWND/300ms验收仍待完成。
- 组内选择：原 Python planner 的 50 案与 C# 零差异，7 个不可变/所有权检查通过；`runtime-group-choice-final-05` 为 111/111，`workspace-group-feedback-after-05` 为 110/110。WPF 使用真实组内控件、Session 收取和完整 BGRA→frozen BitmapSource→生产 Host 回执，隐藏的 Recorded 素材也可就绪，未创建 HWND 或授 Live。Runtime 正负回执是明确消费者 seam；WPF 另验证实际消费像素逐字节保留、失败消息真正到达页面、显示实际 pending 目标、NONE 时保留意图提示和恢复选择。
- 组内选择的修前失败均保留：初版同正门 106/106 没覆盖跨层；原 event-000018/F2 复现已确认 hold 被清，NONE 复现过早丢 desired（`runtime-group-held-before-02` / `runtime-group-none-before-03`）。已确认手选现不重新发现身份；NONE 只撤自动组的临时权限并保留未确认意图，新的同成员登记组才能重试。另一次回执虽然有中文，页面却未消费；`workspace-group-feedback-before-04` 在实际 TextBlock 断言失败，修后同一断言及等待目标的拒绝提示通过。
- 组内素材就绪不是 GPU/窗口上屏；ACK 后原生失败撤显示而不撤已确认身份。跨成员 Reserve、原生显隐/显示失败及延迟 WPF 回调撞关闭/换作者/新帧目前有源码推演，新增离线用例未证明这些原生分支已运行。现有“取消识别”按钮保留全局撤帧语义，不能把成员任务 token 取消保留旧图的代码检查称为该按钮的行为。
- 本轮显隐迁移：`settings-fields-after-unselected-actions-03` 为 Migration.Tests 114/114，`workspace-visibility-after-03` 为 96/96，`runtime-visibility-recorded-02` 为 91/91。实际按钮与默认 F11 的生产策略/WPF 调度/Workspace 消费者共用即时入口，仅 OS 输入源和作用域使用离线夹具；不会取消实际 Session 的进行中采集。录制原图的隐藏刷新继续执行当前帧 Fit/canonical/material，显示恢复复用现有 packet，新局不重置显隐；关闭拒绝迟到写入，正常 WPF 关闭等待已接受的偏好写入。字段保存只覆盖所选值，保存忙时不排队，其他草稿保留。`settings-fields-before-unselected-actions-02` 和 `visibility-startup-before-01` 分别保留了未选中坏快捷键阻断保存、初次载入前切换丢失的修前失败。没有调用 Show、安装或操作游戏；原生悬浮窗即时显隐、真实 F11 和实机像素仍未知。
- 顶层 LiveRuntime 原图整链 runtime-recorded-integration-08-final 为 79/79：S1 315～319 连续观察、C1 人工确认、323 独立目标楼层、困难未确认候选、作者偏好不重捕获/重贴合、不可用材质切回、关闭和资源直接撤回；S0 实际完整 rank/native/登记组消费者、人工 A1，以及噩梦侧门/困难正侧门的自动身份→Host→canonical→material 消费。登记组的显示参考与唯一地图分开。仅替换捕获端口，Recorded 始终没有 LiveAuthorization；尚不代表真实捕获、原生浮层或游戏验收。05 保留了把连续观察误按独立扫描顺序比较的测试失败；06 以原 Python 会话调度器独立计算完整顺序，未放宽顺序或支持名单断言。08 包含最终楼层诊断保留与完整 3×3 矩阵断言。
- S0 当前完整 all251-csharp-s0identity-02 已逐张执行全部 251 张：197 张进入识别、54 张保留未决、285 次候选 Fit，执行异常/逐字段差异/本次冷流程覆盖缺口均为零。未决及无支持结果按原程序保留，不是 251 张识别成功。01 的两处 float32 分数差异定位为 NumPy 2.2.6 的 float32 分位虚拟索引语义，修后两个真实 patch 的中间像素/向量/720 分数逐位一致，原 37 案回归也零差异；没有放宽阈值或断言。首轮 host-only 的 66 差异/197 caller 缺口及索引维度失败证据均保留。pooled point collection 读取器局部支持实际 316416 行，保留通用图像维度及字节限制。
- 困难资源声明竞争者目标层为 null 的真实 147 正负例，原 Python producer/consumer 与 C# 完整入口一致：正向 D15 唯一，D16/1 声明未备齐后撤回所有 hard identity，保留 D15 几何候选，四个矩阵最大差为零；跨语言容差为 1e-9。夹具使用独立副本；原资源字节/时间及 link count 回读记录单独保留于本地证据。
- S0 首次楼层观察复现 1038.99ms、准备总计 1176.48ms 超过原 1050ms 截止。把原有不可变字体模型初始化移到资源绑定后，新的冷进程同图观察为 78.60ms、准备 175.05ms；没有放宽截止或识别阈值。这是该次原图回放测量，不是实机延迟基线。
- Material 当前完整 workspace-identity-material-07-full-after 为 80/80，带真实资源与当前录制客户区。05/06 保留了新完整 rank/fit 后楼层诊断字段丢失的失败，修正在 rank、S1 scan 和最终 Fit 保留本次观测来源，原 WPF 断言未放宽。原生 800×560 固定保存栏/图库标签及连接按钮定位专项的先前可见检查仍保留，没有新做一轮外观改版。只有隔离测试资料，没有连接游戏或启用输入，不代替用户体验验收。
- 图片生产 display-material-csharp08-after-lowbit 为 261/261，保留 3/252 和 4/261 的修前差异。独立 Pillow 12.3.0 实际打开 8/16 位及新增 1/2/4 位灰度透明色键 PNG，逐值核对 RGBA 与完整原 producer 输出；保留其 mode 1 与 mode L 对非零透明色键的实际区别。小 IHDR/巨 IDAT 的坏可选 icon 对照原库 None，完整输出仍与无 icon 像素一致；主图截断和超预算 icon 仍拒绝。真实 6000×6000 PNG 冷入口由先解码后拒绝（峰值工作集约 255.6MB）改为解码前拒绝（新进程约 112.7MB）；PNG/JPEG/WebP 头探针不等于所有 codec 的硬分配上限，以上不是所有图片格式的等价证明。

- 用户要求停止后，两批采样已实际停止并排空：34 + 371 = 405 张原始客户区，包含 S0 噩梦 109、S0 困难 142、S1 困难 62、S1 噩梦 92 张。所有原图建立回放索引，18 张重点样本仅是优先标注，不是白名单。原图、用户确认和测试结果分开保存，确认答案不注入自动搜索。
- `all405-original-replay-01` 已逐张执行原 Python 生产观察/识别：197 张 S0 进入真实下游 fit（284 次候选 fit，184 帧至少一个支持结果）；129 张 S1 完整扫描（1880 次候选 fit，101 帧至少一个支持结果）；79 张开图或楼层未决保留原因。脚本异常为零，不表示未决/无支持图已贴合成功，也不表示 C# 或锁图后稳态验收。
- 新 S1 C# 组件的最终 `s1-all-csharp-05` 已逐张回放全部 154 张 S1 原图，129 张执行完整扫描、25 张开图/楼层未决；支持名单、矩阵和数值残差与原 Python 对照通过，未决样本保留，不表示全部识别成功。此前一张约 0.0553 像素的差异由 OpenCV 4.13 轮廓简化与原 4.12 语义差异产生，已以实际 242 节点/36 链和 101 个源多边形对照定位修正。另有 94 步状态、10 个资源归属反例通过。S1 扫描和锁图后目标楼层 Fit 已接入 LiveRuntime，主程序集成回放正在验证。
- AlignmentCore/RoundLayerGroup 移植的原 Python 对照 465 项及 18 项回归通过。同步事件重入与嵌套 typed-buffer 别名已先复现再修正；独立审查新增的数字字符串转换差异也有修前失败，修后 3 项原反例及 30 项源函数计算的数字语法对照通过。Runtime 所有者已接 canonical 判定、授权、提交和撤回；授权与显示 material 分离，原图集成回放正在验证。Recorded/Synthetic 不授予 LiveAuthorization。
- 浮层离屏验证通过 54 项生产投影/票据检查及 120 项跨客户区 Stage 撤回回归；只有 OS 窗口边界被替换，不代表焦点、多屏 DPI、捕获排除或游戏可见像素通过。WPF 已接独立于页面可见性的 overlay host，前台/当前帧复核与捕获期间隐藏 lease，主机尚待实际验证。完整作者显示选择修前 232/233，修后 `display-selection-csharp12` 为 233/233；实际图片生产、原 Pillow 像素及失败恢复 `display-material-csharp02` 为 244/244。原生浮窗与完整作者 UI 消费仍须分别验收。

- 资源目录声明 254 项；262 条缺图问题分为 130 个楼层 image 与 132 个 preview 缺失。`Ready` 只是绑定声明/路径状态，不代表 254 个数组、所有坐标桥或完整资源链均已验收。compact shared_mask 的 clean/validation 数值相同来自显式角色声明，不是两份独立几何证据。
- 设置测试不等于完整设置消费者已迁移。尚无跨进程 compare-and-swap 保存保证，祖先 junction 路径防护与 S0 路线偏好投影仍需接入；当前只写显式实验目录。
- 当前窗口身份复核包含 HWND、进程启动、PID、线程及类，能拒绝跨进程陈旧候选；尚未监听窗口销毁/重建，同进程同线程同类复用 HWND 的生命周期缺口仍开。共享捕获 port 只在采集时要求目标前台；之后按绑定/几何/帧代际校验，允许返回助手确认候选。单次预览不替换运行帧。WGC 缺能力才选择 GDI；设备失败或上下文变化不自动绕到 GDI。原 overlay-exclusion fallback 仍缺。
- 首个界面预览因图标引用相对路径错误退出，已修正并通过真实 WPF 构造回归；构造/导航测试没有调用 Show，不能代替可见界面检查。WGC 诊断中默认 WPF 自有窗口原生画面白/黑而离屏逻辑图正常；同一程序显式软件呈现后真实 WGC 全通过。没有修改应用的全局渲染偏好，不把默认失败改称成功，也未证明游戏后端可用。
- 用户报告的结构黑块来自原二值区域被整片深色填充，已改为浅色区域与实际四邻域轮廓；不修改原数组或将缺失的完整楼层图伪称已补齐。WPF 回归 21 项含实际 catalog → 选择 → 解码失败 → 有效图恢复和设置真实 I/O，没有 Show。用户另在新版独立测试窗口确认“已显示浅灰区域和轮廓”，仅限该预览的可见结果。
- 新增 `StarMap.Vision` 不启动 Python，但几何证据不自动授权地图身份、canonical 位姿、会话提交或上屏。上述完整消费者仍需实现与真实流程验证。
- Vision 固定 OpenCvSharp4 与 Windows slim runtime 4.13.0.20261107，主许可证随项目输出；完整原生组件 build-specific notices 仍需在包含 Vision 的 Setup 分发前补齐。WPF 已通过 Runtime 引用 Vision；当前仍未打包为完整实验 Setup。
- 最终 WPF 冷进程回放 `workspace-runtime-final` 在首次真实 Floor/Main 消费断言处失败，当次未保存该中间状态，原因未确定。随后只增加中间状态记录，不修改生产算法、时限或断言，`workspace-runtime-final-02/03/04` 均为 46/46；这些通过不能抹去第一次失败或证明稳定性。后续需用保留的中间状态定位复现；不为凑通过放宽默认预算。
- 本轮在准备阶段保留探测数、捕获/解码/观察耗时、截止时间和帧号，失败也保存；截止时间仍为原 1.05s。`workspace-phase3-02` / `workspace-phase3-cold-03/04` 均为 62/62，首读均 1 probe，准备 299.90/262.60/269.29ms，MainEntry 46.81/42.85/50.73ms。这是磁盘原始客户区替代捕获的冷进程回放，未包含真实 G、屏幕捕获或可见像素终点，不是端到端延迟或此前失败的根因结论。
- 侧门 9 案数值对照使用 180s 预算；4 个合成全图/缩小图计算约 5.49/9.07/51.98/68.52s，当日真实局部图约 104ms。仅验证默认算法数值、零预算和预取消，尚未证明默认 2s 中途耗尽、运行中取消或性能目标。不得把宽预算重放称为快速扫描通过。
- 显示桥独立审查发现非单位 reference→native 校准时，中性 clean NPZ 被额外施加逆校准。`display-calibrated-before-01` 用原生产 neutral spec 复现唯一失败；修正显示来源域后，同一 oracle 的 `display-calibrated-after-01` 为零失败，不改几何 admission 或阈值。此前没有校准的 391 案不足以证明这个边界。
- `workspace-phase3-01` 的新 raw-null 回归曾误用已有导入副本，按原保护规则正确拒绝二次导入。测试改用独立数据根后验证原始 null 可见、保留原字节及显式清空修复；没有放开覆盖已有副本。另一次新实时采集 `live-nightmare-client-20261109-04` 因游戏不在前台拒绝，界面工具黑图，未计作有效样本。
- 最终独立审查又定位到三秒读取在快捷键结束后可跨入新局，`workspace-delay-before-01` 先复现失败；结束入口统一取消且倒计时绑定原 session/round 后，`workspace-delay-after-01` 为 63/63。原生配置 PostThreadMessageW 的 Win32Exception 也通过实际 WPF 保存→真实主机→仅 OS 源故障 seam 验证：保留已写设置、停止排空、中文反馈、下一次显式保存恢复且不自动监听；`workspace-native-configure-error-01` 为 66/66。没有向系统注入故障或游戏按键。
- 用户随后明确授权观察实际 G 来采集四种赛季/模式样本，当前明确标注 s0/nightmare。独立 `Capture.Tests --watch-map` 已通过真实 G → 生产输入源/策略 → 生产 GDI → 原图/记录链；第一批 `g-map-watch-20261109-01` 38 个 G 终态全部写出、34 张 PNG，4 次取消，源停止与实际 worker 排空成功。原图为 2560×1417 客户区，独立目视确认两例分别高亮 1 与 −1，C# 为 1/B1。这里只证明这些采样/楼层例，尚非四种模式、完整 WPF 输入、自动身份或悬浮显示验收。
- 采样器审查发现 PNG 成功但 sidecar 失败可能失联、整批提前标记日志写完可能丢终态。修正后共享实际存储路径的 8 项合成像素/真实文件系统故障检查通过（`map-watch-storage-01`），失败保留完整像素来源与分阶段状态，停止新采样并通过 summary/stdout 返回未写终态。第二批后来按用户要求停止并排空。初版 01 的 imageScope 文字与既有 Floor 回放器的固定前缀不一致；全量索引按原采集事件/几何与日期回读，未改原始记录。四模式标签由用户确认，不能用检测器猜测的模式覆盖。

本轮可回到的证据：`full-csharp-validation/live-nightmare-client-20261109-03` 是当前原始客户区与采集事件；`floor-python-current-04` / `floor-current-after-raw-01` 是 37 案原入口对照；`main-entry-current-python-03` / `main-entry-current-replay-03` 是当前 MainEntry 对照；`session-after-final-ownership-tests.txt` / `runtime-after-final-ownership` 是 43/30 项最终生产接缝；WPF 各目录按上文分别记录失败和通过。WGC 的 `wgc-owned-fixture-03-software` 与 `03-default` 分别保留成功和失败，均不是游戏验收。旧失败目录保留，不以重试覆盖。
