# 下一版云端集成检查点

本分支是开发集成检查点，不是发布候选、已打包或实机验收结论。基线固定为
`1f3983bd0ac999671f31adc2f8df58cfe262a4d0`，不改动 `main` 或用户安装目录。

## 已纳入的固定源码

按以下顺序 cherry-pick，三次均无文本冲突：

1. 性能、真实进度和游戏启动绑定：`9c4dab293ccc61b5e230c66fd30f8d192530ed54`。
2. 可选备份与卸载资料向导：`bf062657d991d978d3ec4a2fb49f2ea7533d6316`。
3. 升级/卸载 watcher 生命周期接线：`abf3486c4d48a4df010e3d77eebdd41994981162`。

代码检查点为 `a154ea6d385dbb4b8c1dd47e4837dc27be1f3537`。性能固定提交与原开发
`dc2a203` 的树完全一致；卸载接线固定提交与原开发 `d977083` 的树完全一致。
两条完成分支没有改动同一个文件，但 watcher 生命周期是跨文件依赖，不能拆开打包。

## 本检查点执行结果

Linux 云端、Python 3.12.14、PySide6 6.11.1，Qt offscreen/software；使用 fixture 和临时目录，
没有执行 Windows 安装/卸载、注册登录项、真实游戏或操作用户数据。
以下联合命令在上述代码检查点完成 **179 项，全部通过，52.292 秒**：

```sh
PYTHONPATH=app:tests:tools:experiments/qt_quick QT_QPA_PLATFORM=offscreen \
QT_QUICK_BACKEND=software python -m unittest -v \
  test_game_startup test_game_startup_control test_game_startup_service \
  test_qt_startup_state test_qt_startup_splash test_uninstall_data_wizard \
  test_qt_installer_directory test_qt_installer_reuse_guard test_qt_installer_projection \
  test_client_startup_splash test_client_lazy_pages test_client_performance \
  test_client_package_progress test_client_shutdown test_client_composition \
  test_client_package_manager test_client_info test_client_season_preparation \
  test_client_route_plaza
```

实际解释器为云端已有 `.venv-qt-joint/bin/python`。测试输出有 Fontconfig 缓存目录警告，
不影响本次断言；它不是 Windows GUI 像素验收。`git diff --check` 通过。

## 资源兼容集成检查点

- 已纳入资源固定提交 `ac155844819845eda4aedbd9ff15dbb7864d6705`，本地产品代码
  检查点 `b635113`，而不是开发中的移动 HEAD。
- `client.py` 冲突保留 `resource-check` 阶段和 `startup_report`，将兼容报告传到资源服务，
  不恢复旧的构造后立即关闭开屏逻辑。新增离屏接缝测试核对：报告在首帧前到达服务，
  开屏仍等到 `frameSwapped` 边界才关闭。
- builder 冲突合并 watcher spec 和两个生产安装器 include 的 recipe 哈希，
  installer-only allowlist 去掉重复项。相同 watcher spec 没有重复引入。
- 生产 freeze 现在组装独立 watcher，复制前后验证 hash 和 asInvoker；installer-only
  rebuild 验证 watcher 完整库存及精确程序投影，旧记录或源码变化仍须 full freeze。
- 资源迁移只允许受审的 S1 普通整组退役；用户改动/未知约束导致保留，活动资源不健康
  仍阻断。混合 V2 包保留完整作者文件与原依赖声明，仅已验证活动层进入运行时。

最终联合验证分三组，计数是测试执行数，组间有重复覆盖，不冒称独立用例数：

1. 资源、混合包、构建、watcher 库存、卸载合同：211 项，210 通过、1 Windows 原生
   sharing 跳过。模块为 `test_s1_normal_resource_retirement.NormalRetirementTests`、
   `test_managed_base_resources`、`test_s1_production_base_resources`、
   `test_s1_production_builder`、`test_s1_private_packaging`、`test_s1_production_self_check`、
   `test_qt_installer_projection`、`test_generic_author_startup`、`test_resource_compatibility`、
   `test_mixed_retired_author_package`、`test_generic_author_package`、
   `test_generic_author_consumers`、`test_qt_game_watcher_packaging`、
   `test_qt_installer_reuse_guard`、`test_uninstall_data_wizard`。
2. 性能与 Qt 联合：150 项全部通过，48.468 秒。沿用第一检查点命令，去掉第 1 组已执行的 `test_uninstall_data_wizard`、
   `test_qt_installer_reuse_guard`、`test_qt_installer_projection`；增加上述首帧接缝断言。
3. 资源 UI 各模块使用独立进程，共 162 项，161 通过、1 private ZIP 缺失跳过：
   `test_s1_mode_retirement`、`test_client_resources`、
   `test_season_settings`、`test_client_resource_startup`、`test_client_resource_startup_recovery`、
   `test_client_resource_errors`、`test_client_context_failure`、`test_s1_runtime_contract`、
   `test_s1_reviewed_release_policy`。真实 private ZIP 未提供，不将该跳过计为通过。

最初第 3 组出现 4 个测试准备错误，固定 `1f3983bd` 同环境可复现：Linux 没有
`ctypes.windll`/`WinDLL`。仅修改原有测试替身边界：AppUserModelID 在 Windows 才替换，
模拟 MessageBox 的 WinDLL 允许在 Linux 创建。没有改生产行为、删断言或新增跳过。
修正后的启动/恢复/开屏 27 项全部通过，不代表真实 Windows API 已验。

## Tk 清理集成检查点

在 `a8de28976078064473de407625ac041136c47a65` 上依次纳入
`70dfb021d72dc2b18df9cc7827454d60a642d6b3` 与
`b944cf9244bfe44ffe1fd4463003d76fd763ac4a`，本地代码检查点 `e7ff5c2`。
两次均无文本冲突，不改已有资源、性能、卸载或 watcher 实现。

独立固定提交复核未发现阻断新增问题：

- 27 个旧 Tk 模块和 48 个专属测试退役后，Python/spec/workflow/PS1/BAT 中未发现
  生产 Qt/shared 仍执行导入或读取被删模块。历史诊断模块名和 spec 排除项不是运行依赖。
- 资源分支没有改变 `SharedReferenceProvider` 的构造/加载接口。预制工具直接使用该
  provider，不经过客户端模式门禁，保留版本、typed binding、困难库存/桥及 NPZ 回读校验。
- 混合测试迁移使用真实 `_AlignmentHost`、`single_map_structure.solve`、
  `_fit_observation` 等当前消费者，没有复制旧 Tk 控制器。坏图/坏坐标回退仍由
  `test_client_route_preferences` 直接验证，不将元数据 available 当作图片加载成功。
- 旧 root-before-solver、精确队列去重、rerank 次数及旧帧消费断言随旧控制流程退役，
  不宣称逐方法等价或当前 Qt 性能因此已验。困难 legacy-identity/native-display 预制分支
  仍缺真实发布资源运行证据。

在合入后的实际代码上重跑（各组有重复覆盖，不合计为独立用例数）：

- 性能/Qt 联合 150 项全部通过，49.243 秒。
- 资源/构建/卸载 211 项：210 通过、1 原生 Windows sharing 跳过，12.743 秒。
- 清理扫描、6 个真实资源 fixture、spec、图库、共享恢复/显示聚焦 44 项全部通过。
- `test_build_custom_map_package`、`test_extend_custom_map_package`、
  `test_incremental_editor_connections`、`test_optimize_map_packages`、`test_public_tools`
  共 37 项通过；其执行路径需包含 `tools/maps`。
- 23 个已迁移测试模块逐模块运行共 136 项：134 通过、1 map29 外部资源缺失跳过、
  1 既有并发断言失败。失败为 `test_native_similarity_runtime` 的
  `test_digest_cache_is_published_once_after_local_insertion`，停在 `b_ready.wait(3)`。
  固定 `1f3983bd` 同环境单项复现相同失败；基线导入时仅用空模块替代该单项不使用的
  旧 Tk 入口以避开 Linux `winsound`，实际 native cache、Fixture、setUp 和测试正文不替换。
  Fixture、setUp、失败测试与基线的 AST 一致。没有删测、skip 或放宽断言。
- 当前 Qt 作者/重投影/导入/资源租约/登记组/队列/会话 7 模块各独立进程共 65 项全过。
  初次组合进程因不同测试的 QCoreApplication/GUI 单例混用而中止，该次不计通过；
  随后按各模块既有独立入口执行，不改变产品或测试断言。
- `tools/qt_client_entry.py --self-check` 返回 ready，Tk/原生输入/窗口/监听/捕获均未启动；
  预制工具 `--help` 与 `git diff --check` 通过。Fontconfig 及图库销毁时空 context 警告仍记录，
  不当作真实 GUI 验收。

上游 PR #100 的 Windows 成功记录属于其固定清理提交，不冒称整个新集成树已跑 Windows。
本轮仅上述针对性联合回归，不是全库、真实游戏或 Setup 验收。

## 缓存并发断言与 season-ui 排查

以远端集成检查点 `7a622c0ce4760687b6578b84cd32aa5155ca5344` 为基准，
本轮只修改两个测试文件及本文，不改生产缓存、QML 或赛季切换逻辑。
本地测试修复提交为 `b7f8bc3` 和 `17dde7b`。独立复核未发现阻断问题。

### 缓存并发测试

前述 `b_ready.wait(3)` 失败已定位为测试依赖解释器 opcode tracing 的兼容性问题：
当前 Python 3.12.14 中，原追踪器实际收到 call/line/return，却没有用来触发线程同步的
opcode 事件。独立最小探针在 Python 3.13.5 能收到 opcode 事件，不能据此把失败归因于
生产缓存竞争。生产 `_sha` 仍先在局部字典插入 key，再一次发布完整快照。

新测试不再反汇编或读取执行帧局部变量。它观察真实 `_digests` 属性赋值，在原赋值完成后
控制两个真实 `propose` 调用的交错，保留实际哈希、缓存和结果，不替换算法。
断言首个发布快照已含完整 stat key 与正确摘要，并且各已发布字典此后没有被原地修改。
等待均有上限，退出路径释放并回收线程。

- `test_native_similarity_runtime` 全部 13 项通过。
- 修复后的原失败场景连续执行 100 次，100 次通过。
- 仅在独立测试进程中把 `_sha` 临时改为“先发布空快照、再原地插入”的负向变异，
  新测试在完整 key/hash 断言明确失败；磁盘生产源码没有修改。
- native similarity、显示、review 生命周期、保留显示来源与恢复残差合同相关 28 项全部通过。
- Python 3.13.5 本机环境缺少 `cv2`，未在该解释器执行完整模块；以上执行使用 Python
  3.12.14、PySide6 6.11.1。没有将缺依赖或 Windows 平台检查计为通过。

### PR #99 的 Windows season-ui

历史失败来自固定 `1f3983bd0ac999671f31adc2f8df58cfe262a4d0` 的
[Windows job 128263226870](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42326602229/job/128263226870)：
context superseded 场景第三次选择 S1 时，`qml_item` 返回 `None`，随后
`mapToScene` 报错，尚未进入相应业务断言。原点击 helper 依赖固定 20ms 延迟，
并不保证异步创建的弹出选项已可点击。

修复仅在原真实鼠标点击前等待目标存在、可见、启用且宽高大于零，仍使用既有 5 秒上限，
不删业务断言，不吞异常。历史日志没有指出具体哪个 object_name 缺失，因此这是针对已证实
同步缺口的修复，尚不能称为精确 Windows 环境中已复现并修复全部根因。

- Linux 原上下文模块 9 项本来能通过，未声称自然复现 Windows 失败。
- 显式将目标查询可用性延后 80ms：旧 helper 复现同类 None 错误，新 helper 通过。
  目标始终缺失的负向场景仍在等待上限失败。
- 原失败场景分 10 个新进程重复执行，10 次通过。
- 最终合入树的上下文失败与赛季准备模块共 16 项全部通过，33.535 秒。

上述是 Linux/offscreen 验证，不代替最终远端同一 SHA 的 Windows season-ui CI。
旧提交的 workflow 重跑也不能验证当前补丁。仍需在最终集成提交运行 Windows 检查。

## 后续综合检查点：季节根因、回滚与留样

上述 `61132f6e` 检查点之后，真实 Windows CI 没有替旧推测背书：

- 草稿 PR #101 首轮固定 `61132f6e` 的五个工作流通过，joint 8/9 组通过、season-ui 失败。
  [失败 job 128326024984](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42346738760/job/128326024984)
  确认第三次 S1 选择时选项持续不可用，单加等待没有修复根因。
- 仅加观察的 `14a689fd` 六个工作流及 joint 9/9 组通过。这不是根因修复，读取 popup 或
  增加观察耗时可能改变竞争时机。原失败保留。
- 独立诊断分支 `356882a2` 的四组 Windows 对照中，baseline 和 popup 组失败，geometry
  和 render 组通过。[baseline 两次自然失败日志](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42348036786/job/128329946305)
  确认真实 ComboBox 已收到第三次点击，随后旧上下文准备 timer 派发 inspect，busy 使
  selector 禁用并关闭 popup。坐标稳定、无双击。诊断 workflow 留在独立分支，未纳入集成。

季节生产修复来源 `3293273dccb1c2f218af47e48ab37a6e44267e96`：只给上下文触发的准备
标记 context-only，timer 到期重新检查当前 applied season/mode 的 prepared 状态，丢弃
不再需要的旧工作。启动和显式资源变化仍强制检查，显式请求不会被后续刷新降级。
已删除临时 popup 预读诊断，没有延长等待、重试点击或绕过真实 UI。真实回归在 popup
打开后让旧 timer 到期：旧产品红，新产品保持 popup 并不重复 inspect；显式资源失效、
新/缺失/不匹配 context、失败不循环、关闭和迟到刷新均保留验证。独立复核通过。

资源回滚修复独立来源 `2eb6131b459e605b07d31405e3626e863a8b742c`：只对无旧对象、
目标不存在、父目录也不存在的无操作回滚跳过目录 fsync。路径/链接完整预检仍在前面，
已有父目录仍同步，外来对象、备份变更及真实同步失败仍保留 pending。旧回执回滚问题在
POSIX 基线复现，修复及负例经独立复核；它不与留样 P1 混记。

贴合留样已纳入上游汇总 `0db531d3` 的最终树并修复审查拦下的 P1，未重复挑入上游三笔
原提交。本地来源顺序为 `2f0c5d9` → `6259f9b9` → `99bca8d9`，最终上游集成树
`6df2683413e7806c06e65e8205fdeef9ba0e3f5a`。原 P1 让诊断等锁先于 arrival 登记，可能使
旧 map 越过 end_round 重新捕获及授权显示；追加修复先登记原 arrival，再选择/初始化
诊断，原 cutoff、birth-ticket、退役 guard 和 ownership finally 保持。独立旧树红例、
48 个子例、实际 active 起点和独立复核结果见 [留样集成记录](g-cycle-integration-20261107.md)。

三项在当前实际组合代码检查点 `eaa6191` 上再次验证（组间有重复，不相加为独立用例数）：

- 留样记录首组命令加 `test_client_g_cycle_admission test_s1_preferred_candidate
  test_client_context_failure`：473 项，467 通过、6 平台 skip，64.448 秒。
- 本文最初性能/启动/卸载联合命令：184 项全部通过，53.492 秒。
- 资源/构建/卸载原 211 项命令再加 `test_managed_base_absent_rollback
  test_legacy_receipt_startup_retention`：229 项，228 通过、1 原生 Windows sharing skip，14.390 秒。
- 独立到达竞态脚本的未激活和真实 active 起点共 16 组合全部正确 stale、零新增捕获、无显示授权。
- 27 个变更 Python 文件 compile、`git diff --check` 通过；源码 self-check ready，
  Tk、原生输入、窗口、监听及捕获均未启动。

历史扩大回归中的真实回滚失败已由本轮专项验证关闭；17 个 Linux ctypes 失败和缺
PyInstaller 的限制仍不计通过。首帧组合测试含 Mock 构造器和手动回调，不冒称真实
frameSwapped 信号、显示器像素或 GUI 验收。最终组合 SHA 的 Windows CI 需另行核对，
不能沿用 `14a689fd` 的成功代替新生产修复及留样组合的结果。

原 joint 九组没有选择新增留样、arrival 和回滚测试，因此另加
`next-version-contracts` 组，原九组和固定旧基线不变。新组只执行源码测试，不构建或发布；
仍 checkout 精确 PR head、使用只读权限，逐模块保留每个失败。27 个测试选择器经独立
loader 检查有效、组内不重复；本地按同样的逐模块独立进程运行共 288 次执行，286 通过、
2 平台 skip。与其他组存在交叠，不能相加为独立用例数。其 Windows 结果须绑定新增组后的 SHA。

## 新 Windows 合同组发现的跨平台缺口

固定 `beb65b69` 的五个工作流通过，joint 原九组通过（含 season-ui），新增合同组失败。
[job 128333466118](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42349187681/job/128333466118)
真实执行了留样/arrival 合同并通过，但暴露以下此前未被旧集合覆盖的问题，未删组或跳过失败：

1. Windows 目录 sync 是 no-op，普通文件占据父目录/更高祖先时，原回滚未显式拒绝。
   来源 `79096b61` 在整体预检及每项写入前对全部词法祖先做 lstat，存在者须为目录且非
   reparse；真正缺失目录仍允许，权限和 NotADirectoryError 不吞。此前 `61132f6e` 无
   missing-parent 快捷判断时也可复现前两例，不能归咎于先前 7 行修复。可移植 Windows
   语义反例旧 3 红、新绿，独立审查确认旧对象/备份/外来字节及 pending 保护保留。
2. `_local_file` 只 resolve 文件而不 resolve 比较根，合法 Windows 8.3/长路径别名会被
   拒为越界。来源 `649448c1` 保留规范化前的词法路径链接检查，再将根和文件同时规范化；
   补查原本被排除的 root.parent，不能用 resolve 隐藏链接或路径外逃。
3. 作者显示的生产 provider 本已使用规范根；共享测试 fixture 保留临时目录的 8.3 拼写，
   破坏严格 provider 根比较及 mock native.root 合同。同一提交只规范化共享 fixture 根，
   未修改作者运行时、收据或依赖校验。不同安装根仍拒绝，原混合图包文件/收据断言保留。

等价非链接 `alias-hop/..` 在旧源码复现 manifest 越界、binding=None 和混合根断言失败，
修复后通过；外逃、不同根、文件/目录/父目录链接、helper 篡改/缺失、依赖变化仍拒绝。
本地语义替身和路径别名不是 Windows NTFS 的替代验收，仍以新远端 SHA CI 为准。

实际组合代码检查点 `f147719` 再跑：

- 留样/生命周期/赛季：473 次，467 通过、6 平台 skip，63.727 秒。
- 资源/构建/卸载：241 次，240 通过、1 原生 Windows sharing skip，14.689 秒。
- Windows 新组同样 27 模块逐进程：299 次，297 通过、2 平台 skip。
- 本轮 6 个 Python 文件 compile、diff check 通过。各组有重叠，不合计为独立用例数。

路径修复的额外扩展检查还发现旧 `test_qt_production_author_defaults` 九例及
`test_startup_validation_request` 一例失败，在固定 `beb65b69` 同样复现 6 FAIL + 4 ERROR，
未顺带修改或当作通过。Windows 留样模块的两个 POSIX 专属测试明确 skip，不计原生通过。

## 发布前祖先校验补齐

固定 `8161763025e3a2d938c2e4113797c2e2c89cdc04` 的五个工作流通过，joint 9/10 组
通过，只有新增合同组失败。原 `test_ancestor_file_blocks_without_partial_publication`
在 [job 128336852677](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42350317353/job/128336852677)
确认清单已经发布：计划阶段将普通文件祖先下的目标误判为 missing，随后发布清单再遇到
坏祖先。严格回滚正确拒绝不安全路径并保留 pending，但不能据此接受部分发布。

修复来源 `7e3a9471766145339ad46768f0caa5ce2133cfc5`，实际集成代码 `0b625b7`：

- 计划读取目标及旧状态前显式校验全部词法祖先；存在者必须是非 reparse 目录，真正
  缺失目录仍允许，权限错误不吞。
- prepared 之后、第一次活动对象修改之前整批重查全部目标，每项修改前仍重查。
- 原严格回滚整体预检和逐项检查不放宽，未知文件、旧对象/备份变化及不安全 pending
  仍保留。新增准备期坏祖先反例验证无首项发布，外来阻挡仅由测试移除后才能恢复。
- 旧 `81617630` 模块配 Windows lookup/sync 语义复现原 CI 同一断言失败；修复模块
  同一用例通过，四个新增反例也旧红新绿。独立复核无阻断。语义模型不替代原生 Windows。

固定集成代码再次执行（组间重叠，不相加为独立用例数）：

- 资源/构建/卸载：245 次，244 通过、1 原生 Windows sharing skip，14.231 秒。
- 新 Windows 合同组原 27 模块逐进程：303 次，301 通过、2 平台 skip。
- 留样/生命周期/赛季：473 次，467 通过、6 平台 skip，62.779 秒。
- 两个变更 Python 文件 compile、diff check 通过。精确新远端 Windows 状态另行记录，
  不复用旧 SHA 的成功。

这些路径检查不承诺对扫描之后任意外部并发替换的原子性。原有 TOCTOU 窗口仍可能
造成部分发布并保留 pending，排除外来阻挡后恢复；不得把新保护描述为消除了所有并发风险。
本修复不删除旧图包或用户内容，新提出的升级清理另行设计和验证。

## 75d2a750 原生 Windows 检查点

固定 `75d2a750ef3dc657ceb699b7f62a3793936c2f8a`，tree
`c0768063b81031c2c848587bc8582afe3bd769ce`，六套 Windows 工作流全部成功，
[joint 42351447270](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42351447270)
10/10 组成功。新合同组 job 128340281445 日志确认 checkout 精确 head，原
`test_ancestor_file_blocks_without_partial_publication` 及四个新增反例通过；303 次执行，
301 通过、2 个明确 POSIX 专属 skip。season-ui 也通过。其他成功工作流为 hotkey
42351447284、diagnostics 42351447288、manual 42351447304、shortcuts 42351447269、
route plaza 42351447266。PR 合成 merge `d3cbae4308654d01181576148e745953aa870471`
与 head 的 tree 相同；固定 main/released baseline 只算历史对照。

## S0 自动搜索、开箱跨局和新公告独立集成

三项均从 `81617630` 的独立分支完成并同步，逐项核定 diff、来源与测试后接在上述
Windows 检查点之后，未发生文本冲突。没有替换已有性能、资源、卸载、留样或赛季修复。

- S0 来源 `5bd609139f635616a2868eb3e7473f35747a398a`，本地集成 `d3f0740`。
  仅自动兜底扩大到右侧搜索域；默认成功不额外搜索，fixed 不变，用户框选仍优先且
  不改保存矩形，框无效/未读仍保留自动回退。完整 2/1 或 2/1/-1、符号和歧义拒绝保留，
  S1 四层算法不变。诊断只提供实测控件小框，搜索大区仅数值边界；失败不增加大区图片。
  编辑器显示右半全高，以便调整 y>65% 的真实建议。独立重跑 127 项通过，窄宽离屏布局已核。
  离线像素基准每场景各 60 个新旧交替样本，OpenCV 单线程：1920×1080 噩梦 moved
  新 p50/p95/max 为 5.682/6.822/9.169ms，旧 1.745/2.424/2.855ms 但读不到；
  3440×1440 moved 新 14.401/19.013/23.680ms。扩大搜索有计算成本，不冒称加速，
  这不是实际游戏准确率、采集或首次显示像素的端到端计时。
- 开箱来源 `abbebfdebad9af3806688638807cb7a1d9b7fbbd`，本地 `d3ef527`。
  结束/停止/重置立刻撤销输入目标并暂停，但保留开启偏好；只在结束后的新请求、同一
  SessionService generation、提交后更大的 roundId 和非忙碌有效快照下恢复，仍要求
  当前绑定/前台、触发键先释放及无待释放/原生错误。取消、旧 worker、明确关闭和安全
  故障不能由新局自动重启。显示跨局等待与安全暂停/重新就绪两种状态。
  独立 158 项及额外 6 个边界通过；原产品纯行为测试在下一局 running 断言旧红、新绿。
- 公告来源 `7c5dd344b7b97df4c6d8fd886901dbeb881987bf`，本地 `9374110`。
  窗口常驻组件承接新/同版本修订公告，按版本/code/时间/正文内容身份去重，仅实际显示
  后记精确快照，最近 64 条；旧仅版本字段允许迁移时显示一次。隐藏/最小化/引导/关闭
  确认延后，切页不丢，正在看的正文不被新结果覆盖；关闭迟到、异步读写和重试有验证。
  下载仍由用户点击后打开显示快照的白名单地址，标题/正文纯文本，无自动上传。
  上游独立 36 项与四个审查反例通过。

当前实际组合 `9374110` 回归（组间有重叠，不合计独立数）：

- S0/S1 楼层读取、控件点击、就绪、诊断与开箱/session/coordinator：285 项全过，29.026 秒。
- 公告/update/info/组合/关闭/开屏/gallery/诊断：82 次，81 通过、1 原生 Windows 写入 skip，6.411 秒。
- 留样/生命周期/赛季：473 次，467 通过、6 平台 skip，65.141 秒。
- 新合同组原 27 模块逐进程：303 次，301 通过、2 平台 skip。
- 扩大执行 features 全 24 模块：307 项实际执行中 306 通过、1 基线失败；另一个模块
  在导入时因 Linux 无 `ctypes.windll` 退出，没有计作通过或跳过。失败为
  `test_client_live_controls.test_destroyed_and_reused_hwnd_cancels_then_rebinds_unique_new_process`
  的原生绑定探测未回报 lost，导致 stop 调用次数为 0。两项均在独立 `75d2a750` 工作树
  同样复现，相关产品/测试文件无本轮修改；保留 Windows 原集合继续验证，不改断言。
- 11 个变更 Python 文件 compile、diff check 通过。最终 Windows 状态待精确新 SHA，
  不能用 `75d2a750` 全绿代替新增源码验证。

Windows features 矩阵补入 `test_s0_expanded_search`、`test_client_announcement`、
`test_update_service`、`test_client_chest_auto_tap`，原开箱 intent 测试及其他组保留；
features 现为 24 个模块，资源/留样新合同组仍为 27 个模块，不删失败或收窄集合。
新提出的升级旧资源/图包重置仍在单独开发，本检查点没有此功能，更没有执行用户数据清理。

## d3e741c 三功能原生 Windows 检查点

`d3e741c75d5d34ccc07fbce0965724069d763efc`，tree
`2602752fb7d27383bbba5e4d18b393e5769eed7b` 的六套工作流全成功，
[joint 42352213272](https://github.com/chenhuanh9-a11y/xiaochao-assistant/actions/runs/42352213272)
10/10组成功。features job128342576526 明确 checkout 精确head，24模块311项全过，
新增S0/开箱跨局/公告用例均执行，Linux的两处原生环境问题在Windows通过。
资源/留样合同 job128342576625 为303次，301通过2个明确POSIX专属skip，原部分发布
反例仍通过。PR合成merge `13e80a4bec2061c95c647cc0365f5294f29163aa` 与head同tree。

## 图片内存冗余复制专项集成

独立源 `72e726b4a9fb05dc9cef970d809d3d9567b7f506`（基于d3e741c）无冲突纳入
本地 `efda7b9`。产品仅LiveImages 18行变化：新像素仍复制入Qt自有存储，先比较相同
scope及完整字节再决定复用；消费者取独立QImage值、用Qt写时分离共享像素。不改
识别/贴合算法、缓存容量、授权token、暂存接受/拒绝或clear。原UI发布者及QueuedConnection
仍在，未增加并发写者；并发渲染读取、持有快照遇到clear/淘汰及消费者修改均独立验证。

本地 `5fff46e` 单独记录测试补充：旧Runtime→QML夹具缺season，错误二元组被生产
三元组校验正确拒绝；只补实际snapshot的season后，基线和候选完整原用例均通过，
没有放宽产品授权。新增mode/profile/season不匹配拒绝正反例、constBits/bits、非连续
转换源所有权与并发staging/clear三例也独立复核通过。完整QML用例保留1002个pointer包、
像素移动35/15、stable URL、不新ACK/loading和旧frame/floor拒绝。

实际组合：147次聚焦执行全通过（8.704秒，含测试模块导入导致的重复）；473次生命周期
中467通过6平台skip（63.765秒）；27模块资源/留样合同303次中301通过2平台skip。
1080p clear-reopen与staging各1000轮像素错误/联网0，provider结束为0项而已有消费者
快照仍有效。原始合成A/B、RSS清空后反升约8.4MiB的反例、上游46项既有旧资源预期失败
均保留在 [专项分析](../runtime-memory-analysis.md)，不包装成全程内存下降或无泄漏。

features增加完整image-memory/material-identity/aligned-transform，现27模块。最终本地
逐模块复跑375项中374通过、1个已复现的原生绑定Linux基线失败，另floor observation
模块仍因Linux无ctypes.windll导入失败；完整QML三元组修正及全部新增内存测试通过。
原集合不删，等精确最终远端Windows结果。仍未包括用户暂停的贴合次域补丁、升级资源重置或
作者兼容第二阶段；未进行用户Windows工作集/GPU/长期实机测量。

## 作者快照第二阶段集成（内存专项之后）

以远端 `c77f0f9654eb6eeefb5aa52df18255359be167c9` 为固定基线，无冲突纳入
`fe9d53ef55b215616f1d79c39778da9032e1d690`。本地快照源码检查点 `cbf86a8`，
恢复 fixture 修复 `95988fd`，启动/helper/离屏页面保护与 CI 接线 `46daad4`。
完整设计、范围和待办见 [作者快照第二阶段](../resource-compatibility-phase2.md)。

作者显示包使用不原地覆盖的版本及激活记录；同 ID 删层/换格式、损坏图片/不兼容底模
按层隔离。旧版本与编辑内容保留；停用不等于删除，不释放磁盘空间。尚未实现基础底模
不可变快照、历史快照垃圾回收或一键回退，含快照的批量彻底删除明确阻断。

当前组合执行（测试执行数，组间重叠不合计为独立用例）：

- 上游完整核心组 236 次，230 通过、6 原有平台 skip。
- 新增 author-snapshots CI 组 18 模块，159 次，155 通过、4 原有平台 skip。
- 原 next-version-contracts 全 27 模块，303 次，301 通过、2 平台 skip。
- 原 packaging 全 8 模块，145 次，142 通过、3 原有平台 skip。
- 原 season-ui 全 12 模块，94 次全部通过。
- 图片内存/材料身份/真实 Runtime→QML 组合 68 次全部通过，1002 次排队 pointer 移动
  保持稳定 URL/不额外发布或 ACK；不替代真实游戏长期 RSS。
- 新真实快照 pending 的启动/helper 4 项，加实际 QML 停用/取消 1 项独立复核通过。
  页面文案最后补充断言又单独复跑通过。保留文件/取消/外来替换和当前资源身份门禁。
- 两处旧 fixture 失败在未改 c77f0f96 独立复现，18 次中 2 errors；仅修测试状态及
  平台边界后原 18 次全过，未删断言，额外保护真实 Session 的两个资源消费者被锁阻断。

Windows joint 新增独立 author-snapshots 组，原全部组保留。精确最终远端 SHA 的 Windows
结果另记 PR，不用上游或 c77 的结果代替。此检查点不包含升级重置安装器，也不包含用户
暂停的贴合次域补丁；重置与快照仅在不交付的独立组合树验证 pending/lease 并集。
该预检发现 reset 重下载提示漏快照作者的 P2，已交实现方修复，必须在其后续接入前复验。

## 最终验收门槛

生产 source payload/version 锁仍刻意指向旧值，当前整合源码不得构建为正式交付物。
待功能范围与版本固定后再重算并验证锁，不移除或绕过门禁。资源源锁从 564 项减至
372 项、普通底模减少 51,409,317 未压缩字节；这不是 Setup 最终体积或升级磁盘回收量。

后续先核对最终远端同一 commit 的 Windows CI。每次冲突后检查实际调用链而非仅接受文本合并。
最终远端同一 commit
仍需 Windows 编译、Setup 独立安装/升级/卸载、普通账号登录/UAC、真实 GUI 和游戏验收。
