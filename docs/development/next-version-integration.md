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

## 尚未纳入与最终门槛

- 贴合证据与回放：本地固定修复已有，但源码同步权限仍待确认，未纳入或代推。
  预期交叉为 `client.py`、
  `diagnostic_schema.py`，以及资源分支的 `client_live_runtime.py`。

生产 source payload/version 锁仍刻意指向旧值，当前整合源码不得构建为正式交付物。
待功能范围与版本固定后再重算并验证锁，不移除或绕过门禁。资源源锁从 564 项减至
372 项、普通底模减少 51,409,317 未压缩字节；这不是 Setup 最终体积或升级磁盘回收量。

后续顺序：确认贴合源码同步权限并审最终固定提交，随后纳入并重跑
受影响的联合检查。每次冲突后检查实际调用链而非仅接受文本合并。最终远端同一 commit
仍需 Windows 编译、Setup 独立安装/升级/卸载、普通账号登录/UAC、真实 GUI 和游戏验收。
