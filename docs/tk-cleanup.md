# 旧 Tk 页面清理

基线：`1f3983bd0ac999671f31adc2f8df58cfe262a4d0`。本次仅源码清理及回归迁移，不合并、构建、安装或发布软件。

## 入口与保留边界

当前源码/打包启动边界是 `tools/qt_client_entry.py`，客户端由 `experiments/qt_quick/client.py` 及其 Qt 服务组成。调用图检查未发现 Qt 生产代码导入或读取旧 `identityv_daily_assistant.py`；两处 Qt 测试对旧入口的 AST 依赖已改成实际 native resource/provider 契约，或随旧 Tk 设置写入器退役。

移除 27 个 Tk 专属模块：旧主入口、两种独立试验窗口、桌面页面/教程/引导/控件/图标包装、图库/资源管理页面、自定义路线与布局编辑器、手选窗口及 Tk mixin、悬浮提示/状态/赞赏窗口、旧专属 GUI 审计运行器。旧 `tools/build.py`、`xiaochao-assistant.spec`、`draw_custom_routes.py`、`preview_support_window.py` 同时退役，不保留指向缺失页面的入口。

保留 Qt 页面和服务，以及 `app/` 中的识别、贴合、捕获、资源、事务、数据模型和诊断模块。`manual_map_picker_core.py`、`control_layout.py`、`opacity_controls.py` 等无工具包状态规则继续保留。`identityv_live_mvp.py` 虽带历史名称，仍是当前捕获消费者，未删除。资源、PNG/NPZ、教程、赞赏图片与许可证字节未改变。诊断 schema 中的旧模块名仍用于兼容历史日志，不视为可执行依赖。

地图维护工具 `prepare_shared_fast_structures.py` 不再导入旧 Tk 主程序。它复用当前资源 provider，仍不启动窗口、监听、捕获、排名或求解；不改变输出 NPZ/回读格式。不能把完整 Qt 识别器的全部健康要求额外强加给选层预制。真实 fixture 覆盖缺识别文件、坏未选层、目标缺失/版本/坐标/side binding 错误，以及困难桥逆矩阵拒绝。困难原有全库存/版本校验仍保留；legacy-identity/native-display 分支未使用真实发布资源实测。

## 测试迁移，而非保留失效实现

48 个只覆盖退役 Tk 页面/控制器/审计运行器的文件随实现退役。下列混合文件保留并直接测试共享生产代码或当前 Qt 消费者，不复制旧控制器到测试 fixture：

- `test_async_event_log`、`test_diagnostic_export`、`test_diagnostic_evidence`、`test_storage_retention`：日志预算、证据哈希/路径/缺失、真实导出与 Qt 启动清理。
- `test_optional_author_coverage`、`test_difficult_optional_author`、`test_optional_author_registration_merge`：真实 sidecar、精确绑定、导入、模式隔离。
- `test_control_layout`、`test_manual_map_selection`、`test_independent_opacity_controls`：布局 schema、共享分组/分页、透明度调度与保存。
- `test_data_recovery`、`test_game_window_focus`、`test_structure_preview_corruption`：损坏用户图库保留、原生焦点 API、真实压缩 NPZ 损坏边界。
- `test_native_similarity_runtime`、`test_group_review_lifecycle`、`test_native_group_display`：实际相似组/完整竞争、条件域、缓存、共享引擎恢复。Qt `test_client_registered_groups` 使用的资源 fixture 保留。
- `test_retained_display_provenance`、`test_base_display_consumers`：真实 display producer 的精确全缓冲区身份、copy/slice 拒绝、弱引用/有界缓存，以及 Qt 当前作者状态消费者。
- `test_shared_recovery_residual_contract`：真实 `_AlignmentHost` → `single_map_structure.solve` → `_fit_observation`，新增最终消费者拒绝缺坐标来源的 residual。

旧 UI 契约的当前回归入口：

| 退役领域 | 当前实现/回归 |
| --- | --- |
| 作者选择、每层桥、已保存偏好 | `test_client_route_preferences`、`test_client_route_reprojection`、`test_client_live_runtime` |
| 导入/删除确认、取消、迟到结果、资源 lease | `test_client_resources`、`test_client_import_upgrade_parity`、`test_client_package_manager`、`test_client_resource_recovery_lease` |
| 任务排队、关图/换层/结束、旧结果拒绝 | `test_client_session`、`test_client_group_queue`、`test_client_live_runtime` |
| 手选、快捷键迁移、定点与透明度 | `test_client_manual_map`、`test_client_manual_map_integration`、`test_client_settings`、`test_client_reference_presentation`、`test_client_controls_visibility` |
| 图库、同名稳定 ID、只读下载路线 | `test_client_custom_gallery`、`test_client_custom_display`、`test_client_route_plaza` |
| 教程、布局、页面和退出 | `test_client_tutorial`、`test_client_control_stability`、`test_client_shared_controls`、`test_client_composition`、`test_client_shutdown` |

同名图库选择/启用、默认图库不能删除、下载图库只读但可删除的断言，已补到真实 Qt `CustomGalleryService` 测试。旧 Tk 九项快捷键迁移会增加 `Alt+1` 的断言不搬到 Qt：当前 Qt 的明确合同是保留用户原九项键位，不擅自新增绑定。

这不是逐个旧方法与新方法的行为等价证明。旧 root-before-solver、exact equivalent first-pose queue 消除及 Tk rerank lane 调用次数属于旧控制流程；未用源码移植或假消费者宣称当前 Qt 具备相同性能。共享核心与当前 Qt 正反例保留。

## 验证与未测范围

Linux、Python 3.12.14、PySide6 6.11.1、NumPy 2.2.6、OpenCV 4.12；Qt 使用 offscreen/software。正式构建要求的 Windows/Python 3.13 未在此环境执行。

最终 53 个模块、543 项：511 通过、2 条件跳过、30 失败。另 37 项地图维护工具测试全部通过。失败与固定基线在同环境一致：

- 28 项 map-open/recovery 测试在准备阶段需要 `ctypes.windll`，Linux 无此 API。
- `test_client_live_controls.test_destroyed_and_reused_hwnd_cancels_then_rebinds_unique_new_process`：原基线同样是 `session.stop` 调用次数断言失败。
- `test_native_similarity_runtime.test_digest_cache_is_published_once_after_local_insertion`：原基线同样在 `b_ready.wait(3)` 失败，测试正文未改，未 skip 或放宽。

新增 9 项清理回归（含 6 项真实资源 fixture）检查删除路径、全源文件无 Tk/旧模块导入、工作流模块名存在、共享核心独立导入，以及预制工具资源边界。Qt spec 的 support/tutorial 资源收集与 UAC manifest 失败闭环测试已从旧 spec 迁到当前 `qt-client.spec`。

可重复的入口检查：

```sh
export PYTHONPATH=app:experiments/qt_quick:tests
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software
python -m unittest test_legacy_tk_cleanup test_runtime_exclusions test_client_custom_gallery -v
python tools/qt_client_entry.py --self-check
python tools/maps/prepare_shared_fast_structures.py --help
git diff --check
```

源码 `--self-check` 返回 ready，`tkLoaded`、`nativeInputStarted`、`windowsCreated`、`listenersStarted`、`captureStarted` 均为 false。仅检查导入与 QML 文件存在，不声称真实 GUI/游戏、完整资源、Windows 原生生命周期或安装包验收。
