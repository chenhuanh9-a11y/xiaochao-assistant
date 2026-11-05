# S1 原生资源与 main 2228 集成

独立候选以 `7ec9b11b96b1dace3e37140a10240fe395e90657` 为基线，合入 main `2228b2655e2df47d86987317efbc1e608e32c2ce`，保留 #93 投稿及 #95 选中点标号。三个文本冲突逐段保留赛季三元上下文和 `refreshDisplay` 语义，不整体覆盖文件。最终提交与检查以本地交付回执为准；没有推送、上传资源、安装或发布。

## 已接通

S0 使用原来的两模式根和资源合同；S1 普通（内部 `difficulty2`）、困难、噩梦各使用 `seasons/s1/<mode>/native-map-manifest.json` 的独立根。不可变 `context_roots` 可显式指定独立资源副本，默认根也已在实验 sandbox 中验证。缓存、pack、typed structure、正门及全层局部轮廓、版本、坐标和 memory 均绑定当前上下文，不合并候选。S0普通组合无效：设置读取保留原文件并提供可修正快照，保存必须组成有效的 season/mode 对。

`S1NativeContext` 校验源版本、实际 OpenCV 版本、完整模式地图/四层范围、引用路径及每个声明文件的字节数与 SHA256。`NativeDifficultPack` 只增加显式 mode/season 输入；复用现有 `MapIdentityEngine`、`SharedStructureResourceStore`、`load_shared_version_context` 与 `_AlignmentHost`，没有复制算法或改变识别/残差阈值。保存帧实际调用 registry 的 `get_engine(mode, season)`，不再借 S0 的无赛季 factory。资源失效后，adapter 在锁内检查代际再发布缓存，迟到旧引擎不能重新填回缓存。

首轮 OpenCV5 离线 alpha/边界 NPZ 仍只用于图库。本阶段生产资源由独立 OpenCV4.12.0 / NumPy2.2.6 环境重建 typed mask、入口及 prepared products，不共用5.0缓存。每层坐标基于同一真实 native canvas 的显式矩阵，跨层投影仍不授权。

四层为源高度4/3/2/1对应游戏层-1/-2/-3/-4，入口在-1。`FloorContract` 登记真实竖排顺序；共享楼层证据、状态转换和显式已验证控件矩形的点击命中可处理负层。S0横排字典和流程不变。S1检测器尚无真实帧/窗口比例验证，不能套横排ROI或猜阈值；旧横排校准在S1及上下文变化后拒绝、清空且不写设置。

资源就绪与 live 就绪分开：S1可保存帧识别和指定地图/层的共享贴合回放；当前 `qt_live_gaps` 是 `s1-floor-controls-unregistered`。界面不因 typed 资源可用而广告 start/map 命令，不启动采集。声明一个 flag 也不能绕过未验证的竖排检测器。没有真实S1游戏输入，不能声称实机识别、错层、点击或首贴/复贴时延验收完成。

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
