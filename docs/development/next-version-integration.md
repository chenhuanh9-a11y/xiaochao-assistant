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

## 尚未纳入与最终门槛

- 贴合证据与回放：等待负责人最终修复和固定提交。预期交叉为 `client.py`、
  `diagnostic_schema.py`，以及资源分支的 `client_live_runtime.py`。
- Tk 退役：等待独立分支最终 CI。测试删除必须只限已退役入口，保留或迁移共享服务和
  Qt 最终消费者断言；与其他分支的 CI/文档/共享测试改动逐项核对。

生产 source payload/version 锁仍刻意指向旧值，当前整合源码不得构建为正式交付物。
待功能范围与版本固定后再重算并验证锁，不移除或绕过门禁。资源源锁从 564 项减至
372 项、普通底模减少 51,409,317 未压缩字节；这不是 Setup 最终体积或升级磁盘回收量。

后续顺序：确认贴合源码同步权限并审最终固定提交，等待 Tk 最终回报，随后纳入并重跑
受影响的联合检查。每次冲突后检查实际调用链而非仅接受文本合并。最终远端同一 commit
仍需 Windows 编译、Setup 独立安装/升级/卸载、普通账号登录/UAC、真实 GUI 和游戏验收。
