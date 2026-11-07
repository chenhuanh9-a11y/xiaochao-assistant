# G 逐次开图测试留样

这是独立的、仅供测试的本地诊断通道，不参与识别判断、锁图或界面结果提交。
初始实现 `46cb3cff` 来自 `d2d30a7af2f9c570254c9e830135b778cb1faeaa`。
下一版在云端独立分支 `feat/locked-fit-evidence-next`，以 PR #99 固定源码
`1f3983bd0ac999671f31adc2f8df58cfe262a4d0` 移植；保留正式单局锁图、启动提示和已有
锁后诊断。该源码不是已发布 1.1.1 安装包的 `161e14f7`，不能把本分支验证套给旧包。

## 开启与范围

Qt 客户端入口 `experiments/qt_quick/client.py` 接受 `--g-cycle-evidence`。
仅本次进程有效，不写入用户设置；普通启动默认关闭。该参数不能与 `--self-check`
或 `--probe-output` 同用。开启时有测试留样提示，退出后需重新显式开启。

- `client_g_cycle_evidence.py` 观察现有调度中的算法输入，不增加截图、键盘监听或网络操作。
- 优先保存实际结构提取器使用的地图 ROI；没有该 ROI 时可保存实际楼层检测控件区。
  `sample_role`、`roi_provenance`、`crop_bounds`（x、y、宽、高）和 `full_size`
  （宽、高）说明来源。每次调度仅保留一个选定的局部图，不是完整桌面、完整客户区，
  也不是完整求解重放包。实际无合法局部图时只保存元数据并标记缺图原因。
- 成功、失败和取消尝试采用同一记录合同；`diagnostic_id`、会话、回合、开图、尝试及帧
  标识用于关联。保存成功不等于算法正确或已显示正确像素；`dispatch_ms` 不是端到端显示延迟。
  `frame` 只有在运行时观察的采集对象、采集服务代次和已登记输入视图均属于本次捕获时才填写
  该观察的帧 ID；准备/恢复未提交新运行时观察时明确为 `unknown`，不能沿用上一成功帧。
  `capture_generation` 是采集服务代次，不是每次截图的唯一编号。
- 局部图仍可能含昵称或聊天，不承诺匿名。数据只在本地保存，不自动上传，也不加入普通诊断 ZIP。

### 明确选择完整求解输入

仅在需要检查多区域选择、裁剪或 HUD 污染时，同时提供
`--g-cycle-evidence --g-cycle-full-input`。第二个参数不能单独使用，不会写入设置。
启动警告会明确说明保存的是完整游戏客户区，可能包含昵称、聊天及其他游戏内信息。
它复用本次实际进入结构求解器的已有帧，不新增截图、捕获窗口、桌面采集或上传。
普通 `--g-cycle-evidence` 继续只保存局部图，不会因为诊断失败而扩大区域。

- 完整模式在实际求解入口保存原始尺寸 BGR 像素，PNG 无损；`sample_role=solver-client-input`、
  `roi_provenance=existing-solver-client-frame`、原始/PNG 哈希及零原点边界说明其范围。
  `input_scope` 仅表示本次请求的模式；准备失败时仍可能只有楼层控件局部图或无图，必须检查
  `sample_role`、实际尺寸及缺图原因，不能仅凭请求模式宣称完整。
- 沿用单输入 8 MiB 限制；例如 1280×720、1920×1080 可容纳，2560×1440 原始 BGR 超限。
  超限明确记录 `roi-byte-limit`，不偷偷缩放、裁剪或补黑边制造可重放的假全帧。
- 保存最多 16 个实际结构观察区域的 XYXY 边界，额外区域计数；只接受同次采集及其受控副本。
  来源不符的同尺寸数组不被接纳。下一次采集清空旧像素、区域及求解摘要。
- 正式 S1 锁定状态使用运行时的公开身份合同，不再只看旧 `_durable_map_id` 字段。
  试验模式可以采集同次 S1 求解的有限矩阵、先验资源绑定及拒绝阶段，不依赖自动失败留图开关。
  仍复用 `s1_fit_diagnostics.safe_evidence` 的字段白名单、容器预算和 6144 字节限制，不保存
  任意字符串、路径或数组。收集不改变地图、楼层、身份、求解门槛或显示授权。
- 完整帧有助于离线检查观察区域，但完整求解重放还需相同资源、版本、先验和运行路径；
  当前边界列表及诊断仍会截断。不能声称已获得完整游戏调度重放或正确位姿真值。

已有 `tools/replay_s1_crop_observation.py` 支持 `--case <case-UUID.json>`，读取同名 PNG，
先核 schema、尺寸、PNG 哈希和解码后 BGR 哈希；再进行原有的观察量回放。不认领或修改存储文件，
不把局部图填充为全客户区。`fullInputRecorded` 表示经核对保存了完整求解输入，
`fullSolverReproduction` 始终为 false；仍需独立验证正确地图、矩阵和最终显示。

结构 ROI 使用明确的已提取区域入口：只在现有像素内计算原 live map color mask、最低 300 像素
支持和 3×3 膨胀，沿用现有 3 像素未知边缘及观察链算法。不再把紧裁图当作缩小的客户区，
重套组件宽度不得超过客户区 58%、高度不得超过 62% 的整窗过滤。输出
`componentEligibilityReplayed=false` 和 `componentFilterContract` 表明整窗组件资格并未复现，
不代表所有保存区域都通过了原完整检测。原有 `--sample --crop` 也按显式 ROI 合同处理；
只有经 `--case` 验证为完整客户区的输入才继续走原整窗组件入口及过滤。
楼层控件局部图不能当作结构 ROI 回放，`--case` 会明确拒绝。

ROI 内的边界采用局部半开坐标 `(0, 0, width, height)`，不分配补黑边的原客户区画布。
链中心用 OpenCV 像素中心约定逆缩放，再加保存的客户区原点；输出同时保留局部和客户区坐标。
裁剪外没有像素证据，不推定外部区域、地图身份或正确位姿。

```sh
python tools/replay_s1_crop_observation.py --case case-UUID.json \
  --reference reference.npz --output replay.json
```

示例文件名应换成真实案例文件。用于回放的参考 NPZ 必须另核资源身份，脚本打印的文件哈希不等于
资源版本已验证。不得将原始游戏截图、参考资源或用户诊断加入 Git 提交。

## 存储、上限与退出

目录为 `self.paths.work_root / "g-cycle-test-evidence"`。
`g_cycle_evidence.py` 写入 `case-<UUID>.png`、对应 JSON 和
`.g-cycle-ownership.json`。PNG 无损保存；JSON 包含原始 BGR 像素和 PNG 的 SHA-256、
有限的身份/状态/数值求解摘要。目录所有会话合计最多 100 个案例、256 MiB，含登记开销预算。

- 适配器每次调度最多保留一个 8 MiB 裁剪。后端单 ROI 上限 12 MiB，单案例上限 16 MiB。
- 单个写入线程异步编码和写盘（Windows 尝试降低线程优先级）；待处理上限 2 个任务（含正在处理的任务），
  入队原始像素及元数据预算 32 MiB。此预算不是整个进程的 RSS 上限，编码还有临时内存。
- 超过 24 小时的案例只在**后续写入时**尝试清理；空闲或退出后没有定时清理，
  因而不保证文件在满 24 小时时自动消失。达到数量或容量上限时也尝试淘汰最旧案例。
- 普通 `shutdown()` / `cancel()` 停止接收并排空已接收任务；`disable()` 丢弃排队和
  尚未开始提交的任务，已开始的磁盘提交可能先完成。之后的 G 操作不撤销已保存的历史案例。
- 入队返回和异步完成回调分别报告接收、溢出、缺图、写入失败与取消；不能把 `queued`
  当作已落盘。接入方应关联两类状态，并在丢样时提示。回调只传有限标识，不传原始异常或路径，
  回调失败不改变识别或写入结果。Qt 接入已把入队/完成结果写入现有诊断流的
  `g-cycle-evidence` 阶段，带 `diagnosticId` / `caseId` / `mapAttempt`；丢样首次显示警告。
  UI 通知队列也限制为 64 条，溢出汇总 `dropped`，现有日志自身仍有容量和写盘失败限制，
  因此不承诺无限压力下每条都持久化。物理 G 在输入层被拒绝或合并、未进入运行调度时，
  不会新增图片；已有输入诊断说明这些输入。`pendingWrites` 包含尚未发完通知的任务，供退出协调使用。

## 文件保护与验证边界

复用已有目录固定、进程锁和文件身份校验机制。未知文件、损坏登记、链接、替换文件或中断写入
均失败关闭，不自动认领、修补身份或任意删除。Windows 淘汰对同一已验证独占句柄执行删除；
POSIX 不实施自动删除，需要淘汰时返回 `retirement-unsupported` 并停止该次写入。

Linux 合成 ROI 测试只能验证其实际执行的离线合同；不能代替 Windows 原生句柄、Qt 生命周期、
真实游戏采集/调度或当前安装候选验收。仍需独立 Windows 原生测试。本文不构成全量测试通过、
源码已提交、已构建或已发布的声明。

需要清空时，先完全退出程序，确认不再有写入进程，再由用户通过文件管理器手动删除上述
**整个专用目录**。不要只修改或删除登记文件、单个 PNG/JSON，或向目录放入其他文件；
不一致目录将阻止后续留样。不要删除父级 `work_root` 或其他诊断/用户数据。

## 初始旧基线云端验证（2026-11-07）

Linux / Python 3.12、离屏 Qt，以下集合运行 163 项：161 通过，2 项 Windows 句柄相关测试明确跳过。

```sh
PYTHONPATH=app:tests:tools:experiments/qt_quick QT_QPA_PLATFORM=offscreen python -B -m unittest -v \
  test_g_cycle_evidence test_client_g_cycle_evidence test_client_g_cycle_reporter \
  test_client_g_cycle_recovery test_client_live_runtime test_field_failure_samples \
  test_client_shutdown test_client_map_input test_client_map_intent_reset \
  test_client_partial_cleanup test_client_problems test_client_season_preparation
```

覆盖逐次成功/失败、队列溢出和异步写失败关联、像素快照、不采纳同尺寸外来帧、恢复重试、
关闭/换局/迟到结果、普通关闭与直接退出的线程和诊断排空。捕获与求解输入是合成替身。

扩展集合另有 77 项在 fixture 导入 Windows `ctypes.windll` 时失败，未算通过；
其中 `test_client_map_open_recovery` 的 22 项在未改开发基线也同样失败。
另一个未改启动测试依赖 Linux 不存在的 `ctypes.WinDLL`。需要 Windows 环境重新运行，
不能用平台替身结果冒充原生验收。

60 次顺序随机 800×600 ROI（每次 1,440,000 字节）的离线微测：
裁剪快照加后端入队 p50/p95/max 为 0.479/0.902/1.175 ms；后台编码、全量已登记文件校验和写盘等待
为 102.024/125.958/128.116 ms。全部 60 个案例保存。关闭模式的空观察钩子五轮各 200,000 次，
每次中位约 0.051 µs。这不包含完整 G 调度、真实游戏竞争、截图、求解或首次正确可见像素，
不证明 300 ms / 500 ms 目标、错层率或当前机器性能。文件校验成本随已保存容量增加，上限内也可能触发丢样。

## PR #99 基线移植与完整输入验证（2026-11-07）

独立云端 checkout 基于 `1f3983bd0ac999671f31adc2f8df58cfe262a4d0`，旧留样提交移植为
`ea33de21c1f0f2c3a1563805b24333ecaf2124f0`。本轮修正正式锁定状态读取、同次输入溯源、
显式完整输入和有界回放，不修改求解阈值、锁图状态写者、同图重贴范围、作者显示或资源协议。

真实 S1 调用链的合成测试发现：`FrameSnapshot.pixels` 每次访问返回不同的 ndarray 视图。
旧留样钩子登记临时视图后丢弃，再把另一个视图交给求解器，导致后续来源核验拒绝实际输入。
现在只取得一次视图并同时登记及消费它。测试先复现缺图，修正后验证保存像素等于真实被消费视图，
正式锁定 ID 和单图扫描计划不变。这里的“真实调用链”不表示实际游戏采集或原生 Windows 验收。

验证环境：Linux、Python 3.12、OpenCV 4.12.0、PySide6 6.11.1、`QT_QPA_PLATFORM=offscreen`。
以下集合运行 346 项：340 通过，6 项受平台条件限制明确跳过。

```sh
PYTHONPATH=app:tests:tools:experiments/qt_quick QT_QPA_PLATFORM=offscreen python -B -m unittest -q \
  test_g_cycle_evidence test_g_cycle_replay test_client_g_cycle_evidence \
  test_client_g_cycle_solver_input test_client_g_cycle_reporter test_client_g_cycle_recovery \
  test_client_live_runtime test_field_failure_samples test_client_shutdown \
  test_client_map_input test_client_map_intent_reset test_client_partial_cleanup \
  test_client_problems test_client_season_preparation test_s1_fit_diagnostics \
  test_s1_production_final_lock test_s1_formal_round_lock test_s1_formal_static_reference \
  test_s1_map_open_reference_lifecycle test_s1_locked_identity_compat \
  test_pose_prior_contract test_s1_scan_observation_reuse
```

新增反例包含：外来同尺寸数组、超过 8 MiB 的全帧、下一次采集清空摘要、观察区域截断、
完整参数缺少留样参数、篡改 PNG/原始像素哈希、非法案例 ID、超长 JSON，以及普通诊断导出
不带入逐次完整输入。案例读取在解码前检查 PNG 尺寸；回放输出使用同一次有界读取验证的 PNG 哈希。
默认留样仍只含局部图；测试收集不改变自动失败采样开关。

另做两组顺序随机全帧微测，每组 20 个：1280×720 的复制加入队 p50/p95/max 为
0.424/1.927/4.506 ms，后台编码、存储校验和写盘等待为 143.708/164.905/167.239 ms；
1920×1080 分别为 0.866/4.553/8.934 ms 和 323.339/387.857/398.030 ms。
百分位使用 NumPy 默认线性插值，40 个案例均保存。每次都等待前一写入完成，未模拟队列竞争，
不包括截图、求解、显示或 RSS，也不代表锁后 300 ms 目标。连续高频留样仍可能触发有界丢样。

尚未验证：Windows 原生存储/句柄、真实游戏输入和 GUI、当前安装包、完整求解重放、图像真值下的
贴合成功率及错贴率。PR #99 基线本身的联合检查曾失败，本分支定向集合通过不等于其整体验证通过。
完整游戏客户区、同版资源和可信位姿真值仍是后续算法调整的前置证据；不凭单张合成截图放宽误贴防护。

### 独立审查修正

审查复现了准备/恢复采集新像素后仍记录上一成功帧 ID，以及新 `--case` 入口继承旧裁剪回放内核的
整窗相对尺寸过滤问题。另修正合法整客户区已取得但没有可保留区域时的缺图原因：记录
`no-algorithm-roi`，不再误称 `capture-unavailable`。不为诊断推进运行时帧代次或扩大截图范围。

新增测试先在旧代码上确认上述三项失败，再验证修正。720×1280 合成客户区中的结构区经保存为
316×216 原始 ROI，真实 recorder 落盘、双哈希加载和 `--case` 子进程回放后，1.0 尺度的
`queryPixels` 从错误的 0 恢复为原来的 61004，客户端链中心与原输入一致。
这验证的是明确 ROI 观察合同，不是完整游戏或正确贴合结果。独立审查的原始两个复现测试也通过。
最终按上列相同命令重跑扩展后的 351 项：345 通过、6 项平台条件跳过，用时 47.711 秒。
旧坐标换算隔离测试的 mock 目标随明确 ROI 入口迁移，非空链和具体像素坐标断言保持不变；
真实 ROI 的端到端测试不使用此 mock。以上仍不代替 Windows、游戏及位姿真值验收。
