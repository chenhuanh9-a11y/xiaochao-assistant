# 逐次开图留样与下一版联合集成（2026-11-07）

本文保留固定留样独立子树 `99bca8d9561b239d2e293cd019b9302bd19bc581` 的验证历史。
它未包含后续独立的资源回滚和 season timer 修复；三项合入后的当前结果见
[下一版综合记录](next-version-integration.md#后续综合检查点季节根因回滚与留样)。

## 固定来源与范围

- 集成基线：`61132f6e82293f59a9c4ee3f3be966f73d34a10d`。
- 唯一纳入的上游汇总：`0db531d312083c4b623a40de58ab5052ab31d98b`，父提交
  `1f3983bd0ac999671f31adc2f8df58cfe262a4d0`，tree
  `f2f31c603cdc34a6598423751c2cecaabef9c870`。未重复挑入其原三个开发提交。
- 只在独立云端 worktree 集成。没有改版本、旧 source payload/resource lock、构建配方、
  watcher 实现或卸载策略；没有构建、安装、真实游戏操作、用户资料修改、main 合并或发布。
- `test_client_season_preparation.py` 与基线逐字节相同。其他分支的 Windows
  `context-superseded` 问题及其后继诊断不由本次集成修复，也不因这里的离屏结果宣告关闭。

## 冲突决定与调用链

实际冲突只有 `app/diagnostic_schema.py` 和 `experiments/qt_quick/client.py`。

1. schema 同时保留 own-process 性能/startup 的字段、code 白名单和 G 留样的有限标识。
   没有放开路径、用户名、命令行或任意异常文本。组合反例确认两个功能的合法字段共同保留，
   私人字段仍被 `safe_record` 丢弃。
2. 构造函数保留 `g_cycle_evidence` / `g_cycle_full_input` 的显式进程参数和非法组合拒绝，
   同时保留 `create-services` 阶段。入口继续先 `resource-check`，把
   `production_resources(... startup_report=...)` 的结果交给 ResourceService。
3. `_run_primary_client` 不接受上游构造后立即关闭 splash 的旧行。正常启动依然只由主窗
   `frameSwapped` 到 `_main_frame_ready` 标记 `main-frame-submitted` 并关屏；退出/异常仍有
   finally 清理。这个端点是提交帧，不冒称真实显示器可见像素。
4. evidence writer/reporter 加入 coordinator 的有界退出排空，性能 writer 仍在集合中。
   ProblemsService 在证据完成通知之后关闭；部分构造、直接退出、正常关闭及忙写入用真实
   Qt 事件循环/线程池、临时目录和 lease 测试，原生输入与窗口仍为替身。
5. `client_live_runtime.py` 自动合并后逐段核对：留样 wrapper 只包围原 dispatch ownership；
   已退役 S1 ordinary 的 fail-closed 分支仍在原拥有状态的 dispatch 内。进入新的捕获清空
   旧证据，同次输入别名登记、FrameSnapshot 单次 pixels 视图、取消及最终当前性核验保持。
   不新增采集，不改变求解阈值、锁图写者、资源迁移或显示授权。

**后续独立复核纠正：** 上述初步调用链核对遗漏了诊断初始化先于 arrival 登记的等待。
固定 `2f0c5d9` 因此存在旧开图跨越结束本局而复活的 P1 回归，不能把原 429 项通过视为
完整非干扰证明。追加修复与红绿证据见本文末尾，原提交与测试记录保留。

普通进程不创建留样 writer。显式局部留样仍不因失败升级为整窗；完整客户区另需第二个参数。
本次观察 → 有界快照 → 异步本地保存 → 白名单日志，与识别/显示提交分开。回放验证 PNG 与
BGR 双哈希，ROI 不补黑边，`fullSolverReproduction=false` 不变。安全存储和正常诊断导出
不扩大留样范围。watcher 的 freeze/hash/asInvoker/rebuild 收据代码及可选备份卸载代码均未改。

## 旧测试条件迁移

先不修改 fixture 重跑原上游 351 项：337 通过、8 失败、6 平台 skip。八项失败在固定
`61132f6e` 干净 worktree 逐项复现，原因均为旧正例要求已退役 `s1:difficulty2` 开局成功。
没有通过放开生产普通模式、删断言或新增 skip 消除它们。

- `test_s1_locked_identity.py` 的跨模式 unknown/锁定策略改为活动 nightmare/difficult。
  原单锁 ID、失败撤像素、同图恢复断言不变，其继承的正式锁图集合共同使用新运行条件。
- `test_s1_static_reference.py` 的跨模式静态小抄旅程仅使用活动 nightmare/difficult。
  开图、关图仍保留小抄、结束撤销的断言不变。
- 原 `Difficulty2LockedOpen` 三个队列开图/关图/结束反例迁为
  `DifficultEvidenceLockedOpen`，保持正式 retained-lock 和所有原断言，再开启有界留样观察。
  合成 engine adapter 显式消费 telemetry-only `collect_fit_diagnostics`，交给原 scanner 的
  求解参数与算法不变，不伪造 fitEvidence。被 session 提前取消、从未进入 dispatch 的任务
  不要求有新留样。
- 保留 `test_s1_mode_retirement` 的 selector/provider/旧偏好拒绝与恢复反例；新组合测试进一步
  证实留样开启时 retired map/close/recover 仍不采集、不求解，只留无图失败元数据。

这些 fixture 使用既有合成结构 catalog、原资源 key 和楼层合同，没有导入或修改生产资源，
不是困难/噩梦真实识别质量或 B1–B4 图像真值验收。未改本轮范围外其他旧 ordinary fixture。

## 可复现验证

环境为 Linux、Python 3.12.14、OpenCV 4.12.0、PySide6 6.11.1。
命令在本集成 checkout 根目录执行；Python 可替换为装有同版依赖的解释器。

```sh
export PYTHONPATH=app:tests:tools:tools/maps:experiments/qt_quick
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_BACKEND=software
PYTHON=../.venv-qt-joint/bin/python

$PYTHON -B -m unittest -v \
  test_g_cycle_evidence test_g_cycle_replay test_client_g_cycle_evidence \
  test_client_g_cycle_solver_input test_client_g_cycle_reporter test_client_g_cycle_recovery \
  test_client_live_runtime test_field_failure_samples test_client_shutdown \
  test_client_map_input test_client_map_intent_reset test_client_partial_cleanup \
  test_client_problems test_client_season_preparation test_s1_fit_diagnostics \
  test_s1_production_final_lock test_s1_formal_round_lock test_s1_formal_static_reference \
  test_s1_map_open_reference_lifecycle test_s1_locked_identity_compat \
  test_pose_prior_contract test_s1_scan_observation_reuse test_client_g_cycle_composition \
  test_s1_static_reference test_s1_mode_retirement

$PYTHON -B -m unittest -v \
  test_client_g_cycle_composition test_client_partial_cleanup test_client_startup_splash \
  test_client_performance test_client_resource_startup test_client_resource_startup_recovery \
  test_client_resource_recovery_lease test_client_package_progress test_client_transaction_retirement \
  test_s1_mode_retirement test_client_joint_diagnostic_schema test_client_diagnostic_integration \
  test_diagnostic_noninterference test_client_preparation_diagnostics test_resource_compatibility \
  test_legacy_receipt_startup_retention test_s1_normal_resource_retirement \
  test_mixed_retired_author_package test_managed_base_resources test_resource_transaction \
  test_game_startup test_game_startup_service test_game_startup_control test_uninstall_data_wizard \
  test_legacy_tk_cleanup test_qt_startup_splash test_qt_startup_state test_packaged_resource_contract \
  test_packaged_product_identity test_diagnostic_redaction test_diagnostic_disk_privacy \
  test_diagnostic_evidence test_diagnostic_export test_diagnostic_storage test_diagnostic_races \
  test_qt_game_watcher_packaging test_admin_manifest
```

第一组最终运行 429 项：423 通过、6 明确平台 skip，用时 49.074 秒。
第二组运行 426 项：404 通过、17 失败、2 错误、3 明确 skip，用时 15.767 秒。
两组含重叠和继承执行，不能相加称为互不重复测试数。第二组的 19 个失败/错误均在固定基线
复现，未删除或更改对应测试：

- `test_client_preparation_diagnostics` 及其导入的 drag fixture 共 17 个失败，原生捕获模块导入
  需要 Linux 没有的 `ctypes.windll`。不是本轮 Windows GUI/游戏通过或失败证据。
- `PackagedIdentityTests.test_production_pe_brand_and_appid_contract` 缺 PyInstaller；未安装依赖
  或把假 PE 检查冒称真实 PE 验收。
- `LegacyReceiptStartupRetentionTests.test_failed_base_addition_rolls_back_and_preserves_legacy_files_and_settings`
  是独立的现存回滚缺陷，不能仅归类为环境缺依赖，见下节。

对照命令（在固定 `61132f6e` checkout）：

```sh
$PYTHON -B -m unittest -v \
  test_s1_production_final_lock.ProductionFinalLock.test_difficult_modes_use_same_locked_unknown_policy \
  test_s1_formal_round_lock.FormalRoundLockContracts.test_difficult_modes_use_same_locked_unknown_policy \
  test_s1_locked_identity.LockedIdentityContracts.test_difficult_modes_use_same_locked_unknown_policy \
  test_s1_formal_static_reference.FormalStaticReferenceContracts.test_all_s1_modes_share_static_reference_lifecycle \
  test_s1_formal_static_reference.LockedStaticReferenceContracts.test_difficult_modes_use_same_locked_unknown_policy \
  test_s1_map_open_reference_lifecycle.Difficulty2LockedOpen
# 8 项，同样 8 失败。

$PYTHON -B -m unittest -v \
  test_legacy_receipt_startup_retention.LegacyReceiptStartupRetentionTests.test_failed_base_addition_rolls_back_and_preserves_legacy_files_and_settings \
  test_client_preparation_diagnostics \
  test_packaged_product_identity.PackagedIdentityTests.test_production_pe_brand_and_appid_contract
# 20 项：1 通过、17 失败、2 错误，失败/错误入口与集成树一致。
```

全部 22 个新增/修改 Python 文件通过内存 `compile(..., 'exec')`，不生成 pycache；
`git diff --check` 与 staged diff check 通过。源码测试不代替真实 freeze、PE 文件或 Setup。
已提交树的复核命令：

```sh
$PYTHON -B - <<'PY_CHECK'
from pathlib import Path
import subprocess
changed = subprocess.check_output(['git', 'diff', '--name-only',
    '61132f6e82293f59a9c4ee3f3be966f73d34a10d', 'HEAD'], text=True).splitlines()
paths = [Path(name) for name in changed if name.endswith('.py')]
for path in paths:
    compile(path.read_bytes(), str(path), 'exec')
print('compiled', len(paths))
PY_CHECK
git diff --check 61132f6e82293f59a9c4ee3f3be966f73d34a10d HEAD
git diff --exit-code 61132f6e82293f59a9c4ee3f3be966f73d34a10d HEAD -- \
  experiments/qt_quick/test_client_season_preparation.py
```

## 保留的资源回滚问题与后续边界

基线和集成树都能复现：先模拟 `references/catalog.json` publication 失败，后续的
`references/difficult-native` 还未创建。`managed_base_resources.py:682` 的回滚循环对该
未发布的新文件仍无条件 `_directory_sync(path.parent)`；`map_package_upgrade.py:79` 的
POSIX `os.open` 因父目录不存在抛 FileNotFoundError，被前者 683–684 行记成
`changed-or-unwritable`，导致 pending marker 保留。只读 instrumentation 已确认不存在的
父目录是直接原因。Windows 的目录 sync 是 no-op，不能据此断言 Windows 同样故障。

没有放宽身份、哈希、链接检查或吞掉该异常。本次不改资源模块。最小后续是针对“较早提交失败、
后续尚未创建目录”的独立回滚反例，区分未发生修改的条目与确需同步的目录，同时继续覆盖真实
删除/恢复失败保留 pending 和备份的 fail-closed 路径。

未验证：Windows 原生安全存储句柄/删除、真实键盘与游戏采集、显示像素延迟、真实资源位姿真值、
压力下 RSS、生成包、升级/卸载实机、完整 solver 重放。现存 Windows season UI 问题仍独立待查。

## 独立复核后的 arrival 次序修复

独立复核发现原 `2f0c5d9fdf28a09a47d83275e202f56838166a0f` 的 P1：留样开启时
`_Trace.__init__` 先等 `runtime._lock`，之后业务 `_dispatch_with_ownership` 才登记
`arrival_serial`。若旧 map 等锁期间 main 重入 `end_round`，它释放后得到新的 serial，
绕过既有 cutoff，重新开局、捕获并授权显示。留样关闭的相同操作则正确 stale。
`recorder.accepting` 也可能在原登记之前等待自己的锁。该路径原样来自上游 `0db531d3`，
不是两处手工冲突引入，但相对 `61132f6e` 是本次引入的真实回归；“wrapper 包围原 ownership”
不能证明保序，前述初步审查对此漏检。

追加修复不改写 `2f0c5d9`，也不改取消 guard 或减少留样功能：

- `dispatch` 先进入原 ownership，保留原 S1 action 标记、原子 serial、request id 和线程内
  arrival 登记的次序；之后才选择 recorder、读 accepting 或建立 `_Trace`。
- 原 cutoff 和 birth-ticket 检查完整移入 `_dispatch_after_arrival`，证据适配器只调用已登记
  operation；初始化失败也调用同一个 operation，不重新登记 serial。
- 非留样 action 不访问 recorder.accepting，end/cancel 等操作不等待无关诊断锁。
- 原 capture owner 的 request-id 核验清理与 arrivals pop 仍在外层 finally，覆盖业务执行和
  证据完成，不提前释放 ownership。原迟到 finish 测试的等待位置移到新的业务返回边界，
  所有旧回执身份、撤像素和当前新开图断言不变。

新增 `test_client_g_cycle_admission` 三个测试方法包含 48 个子例：S0/S1 × nightmare/difficult ×
已请求/未请求 `start_round` × 留样 on/off，分别等待真实 RLock、recorder.accepting、观察初始化锁后抛错。
这里的 start 请求不等于实际对局已激活，S0 可能还在 roundPreparing；测试变量和报告明确使用请求口径。
捕获/求解仍用既有 synthetic fixture；锁包装仅暴露已尝试锁的事件，实际锁和 serial/cutoff
逻辑均为运行时代码，没有用 sleep 推测先后。每个子例同时断言 stale、零采集、无活动本局、
无显示授权、恰好一个原 arrival 且不大于结束 cutoff；有留样时只允许取消元数据、不带像素。

同一新增测试源对固定旧 `2f0c5d9` 的独立 archive 先跑：三个方法的 24 个 on 子例全部失败，
24 个 off 子例通过。修复树三方法全部通过。复核者原八组合复现脚本另以 `--expect-fixed`
全部通过。另以实际 map 成功并确认 `roundActive=True`、close 后 seq3 重开为起点的八组合
也全部拒绝旧请求、零新增捕获、无显示授权。这些是合成并发合同，不是 Windows/游戏验收。

复现新增定向检查：

```sh
PYTHONPATH=app:tests:tools:tools/maps:experiments/qt_quick \
  QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
  ../.venv-qt-joint/bin/python -B -m unittest -v test_client_g_cycle_admission
```

追加回归沿用本文第一组完整 429 项命令，并在最后增加
`test_client_g_cycle_admission test_s1_preferred_candidate`，覆盖既有候选到达、状态等待、
切季、关图及设置边界。最终运行 461 项：455 通过、6 明确平台 skip，用时 49.838 秒（最终测试维度命名后的重跑）。
四个新增/修改 Python 文件 compile、diff check 通过。AST 对照确认 `_dispatch_owned`、
`invalidate`、原 cutoff/birth/owned-call 整段及 ownership 的清理 finally 均与 `2f0c5d9`
一致，只改变诊断相对 arrival 登记的位置。原扩大联合集合中的 19 个基线失败/错误不因
这次修复而关闭。新旧日志均保留，不将初始有缺陷树的通过结果替换成追加修复的证据。
