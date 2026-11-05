# S1 原生资源与 main 2228 集成

独立候选以 `7ec9b11b96b1dace3e37140a10240fe395e90657` 为基线，合入 main `2228b2655e2df47d86987317efbc1e608e32c2ce`，保留 #93 投稿及 #95 选中点标号。三个文本冲突逐段保留赛季三元上下文和 `refreshDisplay` 语义，不整体覆盖文件。最终提交与检查以本地交付回执为准；没有推送、上传资源、安装或发布。

## 当前增量：人工地图身份与有限自动楼层

本轮以 `bda8b516033afa97d37b2127574e77725966c6ff` 为基线。S1 地图身份仍由用户选择；自动选图、候选扫描和全局 `liveReady` 门没有开启。楼层能力独立于该总门：手动会话不再把入口默认值 -1 当作观察结果，而是读取当前帧完整的负号四层竖列、唯一高亮及独立开图证据。按用户新增授权，私测版对 B1～B4 均自动读层；B1/B2 有保存帧证据，B3/B4 只有合成合同、尚未实测，界面保留该说明。`automaticFloorEligible` 仅表示完整当前帧可供私测消费，`observedFloorValidated` 和 `sourceReplayFloors` 仍只记录 B1/B2 的原图证据；证据不完整、歧义或过期均返回未知并撤销旧贴合。`observedFloor`、目标层、人工地图身份和显示授权分别记录。

选图、显式重新贴合、物理开图与前台返回走指定地图的当前帧采集。既有定时器最多每 0.5 秒维护已开图周期；无开图边沿不得被动复活闭图周期。开图淡入沿用原有有界准备及有限恢复，检查窗口、资源、取消、票据和捕获代次。观察到换层先撤旧姿态和静态参考，再重新拟合；同层续权仍需要全客户端观测域及当前残差通过。没有向游戏发送按键或点击，未注册任何 S1 点击几何。

图库及首页在原离线索引不存在时，可严格校验并只读复用 `seasons/s1/<mode>` 内置 native 清单和 PNG；显示为内置原生底图，不伪称二值结构或作者包。损坏的既有离线索引不会被此回退掩盖，三个模式和 B1–B4 保持独立。未重建地图资源或生成作者收据。

本机测试覆盖数学像素、运行时生命周期、真实资源解码及离屏 QML 接缝；真实游戏采集、DPI、交互式 UAC 和 B3/B4 实图仍需单独验收。保存帧结果也必须使用最终同一份源码复放，不将合成或资源测试称为游戏可用性验收。后文的初版手动记录用于保留此前阶段的证据，不代表当前自动楼层行为。

## 已接通

S0 使用原来的两模式根和资源合同；S1 普通（内部 `difficulty2`）、困难、噩梦各使用 `seasons/s1/<mode>/native-map-manifest.json` 的独立根。不可变 `context_roots` 可显式指定独立资源副本，默认根也已在实验 sandbox 中验证。缓存、pack、typed structure、正门及全层局部轮廓、版本、坐标和 memory 均绑定当前上下文，不合并候选。S0普通组合无效：设置读取保留原文件并提供可修正快照，保存必须组成有效的 season/mode 对。

`S1NativeContext` 校验源版本、实际 OpenCV 版本、完整模式地图/四层范围、引用路径及每个声明文件的字节数与 SHA256。`NativeDifficultPack` 只增加显式 mode/season 输入；复用现有 `MapIdentityEngine`、`SharedStructureResourceStore`、`load_shared_version_context` 与 `_AlignmentHost`，没有复制算法或改变识别/残差阈值。保存帧实际调用 registry 的 `get_engine(mode, season)`，不再借 S0 的无赛季 factory。资源失效后，adapter 在锁内检查代际再发布缓存，迟到旧引擎不能重新填回缓存。

首轮 OpenCV5 离线 alpha/边界 NPZ 仍只用于图库。本阶段生产资源由独立 OpenCV4.12.0 / NumPy2.2.6 环境重建 typed mask、入口及 prepared products，不共用5.0缓存。每层坐标基于同一真实 native canvas 的显式矩阵，跨层投影仍不授权。

四层为源高度4/3/2/1对应游戏层-1/-2/-3/-4，入口在-1。`FloorContract` 登记真实竖排顺序；共享楼层证据、状态转换和显式已验证控件矩形的点击命中可处理负层。S0横排字典和流程不变。S1检测器尚无真实帧/窗口比例验证，不能套横排ROI或猜阈值；旧横排校准在S1及上下文变化后拒绝、清空且不写设置。

资源就绪与自动 live 就绪分开：S1可保存帧识别和指定地图/层的共享贴合回放；当前 `qt_live_gaps` 是 `s1-floor-controls-unregistered`。界面不因 typed 资源可用而广告自动 start/map 命令。声明一个 flag 也不能绕过未验证的竖排检测器。显式手动贴合是独立的当前会话入口，不改变全局 auto/manual 偏好，也不决定以后是否采用自动认图。没有真实S1游戏输入，不能声称实机识别、错层、点击或首贴/复贴时延验收完成。

## 初版手动贴合记录（自动读层前）

先连接游戏窗口，在主界面地图识别页或游戏控制面板点击“准备手动贴合”，自行打开游戏地图，再“选择本局地图”。选图器只显示当前赛季/模式的地图，缩略图来自该 native manifest 已校验的 game-native PNG。软件初始人工楼层为已知主门层 -1；竖列 -1/-2/-3/-4 可随时人工选择，须与游戏当前楼层相同。软件楼层按钮不会点击游戏中的按钮。

`prepare_manual_alignment` 仅建立手动会话并启用已绑定的捕获；选图确认、软件换层及“贴合当前画面”都重新取得当前完整客户端帧。主界面的贴合按钮复用已有的显式游戏焦点恢复和窗口/代际复查。人工 map/floor 只提供目标：`identitySource` / `floorSource` 为 manual，`observedFloor` 保持未知，不伪造 map detector 或楼层观测，也不写S1识别学习标签。换层立即撤销旧pose，清空group/core锚点；每层独立求解，不建立跨层关系。

S1手动求解把当前全客户端观察域显式传入共享 fast/independent structure solver，当前组件与 visible 同时传入每次 residual 和 group tracking；没有结构时立即拒绝，不用 `None` 回退到S0固定ROI。灰度、几何和最终残差阈值不变。首次路径不采用尚未适配该观察域的 steady、入口及跨层恢复分支。只有当前帧 canonical residual、原 core adjudication、捕获窗口及请求代际都通过，才能产生渲染回执。空白/不足结构会拒绝；不同地图或楼层如果共享当前可见结构，人工误选不保证能被软件识破。当前未知的游戏内控件几何不授予自动读层或点击能力。

真实 QtClient/ManualMapPickerService/SessionService 使用实际资源、注入合成捕获的端到端证据保存在忽略目录 `work/s1-manual-integration/`。该验证覆盖选图后 -1 当前帧贴合、软件切 -2 后重新贴合、竖排四层、空白帧撤销旧渲染；合成证据始终 `liveAuthorization=false`，正常 live 显示消费者拒绝将它显示成游戏授权层。实机捕获与窗口上下文仍须单独验证。

## 正门与侧出口

正门由真实20201房型的当前纹理alpha派生，PlayerStart图标本身不作为结构识别证据。`side-entries.json` 是全层局部轮廓检索，不等于语义侧出口。当前包体的场景对象、theme1 无 side model、44 图 door 为空，以及 `process_maze` / `create_indoor_outdoor_inter` 的 DoorType 链共同确认：普通外场只有 -1 主门，没有 S0 的 0/1 外场侧入口。因此不为 S1 补造侧门模板；`layout.door={}` 不能单独支撑这个结论。营地初生采用 p1..p4，20201 的 PlayerStart 是迷宫主门标记，不是出生点。Boss、矿车等特殊传送仍存在，不属于普通外场入口。证据为独立审计 `thy-reader/runtime-evidence/entry-spawn-review.md` 和 `entry-data-audit.json`；原始游戏模块不入仓库。

旧源族/相似组的1/2/B1路径保持S0可选能力，S1不借用。S1的 logical confirmed groups 和 similarity groups 不编新成员。旧 front gate prefilter 的F1/102xx来源不适用于S1负层，实际不缩减本批候选；完整竞争范围仍来自当前 S1 pack。

## 受控回放边界

真实独立资源根加载：S0噩梦26图/78层，困难30图/58个原生typed层（原29号两层缺原生绑定继续明确保留）；S1普通15图/60层、困难14图/56层、噩梦15图/60层。

3模式 × 3比例（1920×1080、1440×1080、2560×1080）× -1/-2层的18个指定地图受控同源donor，共享贴合与最终当前帧残差均通过，最大真值角误差约1.76px。另测噩梦-3/-4层的稀疏结构，只作为指定层拟合样本，不授予地图身份。source PNG同源合成不是游戏实拍，也不是可见端到端时延。

仅20201房间内部、轮廓不足的三个负例均不授予身份。更关键的三个完整20201房间轮廓donor（无邻室）各有14个当前query chains，实际完整评估15/14/15张同模式地图，均无 hard identity。共同正门不足以唯一识别地图；需要当前可见的独特邻室结构。没有调整灰75、固定裁剪、unit60、12unit入口邻域或残差门来凑成功。

S0两张既有记录donor仍检测地图打开，噩梦读1层、困难读2层。结果只对应记录像素回放，不是新一轮实机操作。

证据在忽略目录 `work/s1-runtime-integration/`：`controlled-donor-smoke.json`、`controlled-donor-matrix.json`、`full-entry-only-competition.json` 及最终回归/提交清单。原始游戏资源、donor图和实验缓存不进Git。

## 云端复现范围

纯数据合同、上下文、版本/hash与状态政策可在安装相同依赖的Python环境运行；Qt边界需PySide6及offscreen/software。`test_s1_runtime_contract` 使用临时metadata/控件/调度替身，不能证明真实游戏。现有部分 `test_floor_observation_evidence`、native HWND/快捷键/截图控件测试依赖Windows；在云端必须保留平台skip/失败，不能替换成实机通过。

实际资源回放需经授权的本地 S0/S1资源副本、匹配4.12环境和明示输入。源码patch可独立交接，私人/游戏资源不上传；缺数据的云端只能运行纯代码及合成合同回归，不能复用本地donor结论作为云端实测。
