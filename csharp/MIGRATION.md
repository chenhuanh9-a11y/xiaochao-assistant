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
| 会话/取消/缓存/恢复 | client_session.py / client_coordinator.py / client_live_runtime.py | SessionService 单写者/单 worker/取消/资源 lease/真实回调重入与 Python 纯状态 43 项通过；Runtime 原图回放覆盖显隐、组内选择、canonical/material 与恢复。几何组复用现有 Core RoundLayerGroup，由同一提交包发布；无关系新层成功建新根，失败保留旧组。S0 的0059仅在实际 WPF 素材就绪、Session 已收取结果后记录实际组 owner 的目标层尺度/coverage；新 owner ACK 清旧层缓存，作者重投影保留 owner 且不新建学习回执。S1 素材不请求0059学习。开图准备失败后已接原 8 次/200ms 间隔/2s 有界恢复，每次单帧复用同一求解链；票据绑定本局、输入、资源、作者代际和稳定窗口绑定。隐藏页面的独立 timer/Session 已接默认 S0 被动刷新及两次负帧关图，并同步同一物理输入相位；窗口/几何上下文有效期与最新采集帧分开，求解仍验证最新帧。当前姿态直接续期、S1 专用楼层观察、点击意图策略、保留观测的组内拟合及人工确认重新采集仍缺；Recorded 始终不授予 LiveAuthorization |
| 结构/作者/浮窗/穿透/DPI | client_windows.py / ClientAligned.qml / ClientReference.qml / author_display_policy.py / nightmare_author_coordinate_adapter.py | 显示桥当前 391 adapters/391 bridges/5 native corners/1 非单位校准零差异，完整选择 233 项和素材 261 项通过。Runtime 已接 canonical/material 提交、偏好替换与撤销；浮层已有真实 WPF 主机和离屏几何/票据回归。完整作者交互、参考窗及实机穿透/多屏 DPI 仍缺；素材不授权身份或结构位姿 |
| 账号/路线广场/投稿 | account_service.py / client_route_plaza.py / ClientAccount.qml | 待迁；不以演示数据或按钮提示冒称接入 |
| 教程/公告/帮助/诊断/更新 | client_info.py / update_service.py / ClientHelp.qml 等 | 待迁；关闭/后台恢复、状态与错误路径需覆盖 |
| 自包含 Setup/数据迁移/升级回退 | 当前 Python 安装更新流程 | 待交付完整实验 Setup；干净 Windows/中断/回退仍缺可恢复环境 |

状态随实现与证据更新，不因页面存在就改为功能完成。每次交付记录源提交、资源来源、真实输入、构建、GUI 与未测项。Python 原件、玩家数据和历史失败证据保持可恢复。

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
