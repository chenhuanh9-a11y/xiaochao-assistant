# 星图功能模块与分支维护表

返回[开发 SOP](project-development-sop.md)；全部现存分支的冻结 SHA 与祖先关系见[基线台账](maintenance-baseline-20261106.md)。

## 证据范围与使用方法

- 盘点日期：2026-11-06；只读来源：`reference-independent-isolated` 源码快照。已读根目录 `AGENTS.md`、`docs/development.md`，核对实际文件树、Qt 启动组合、构建入口、依赖清单和 CI 配置。
- 补充核对：已对照真实 Git 目录 `xingtu-development-sop`，文档分支 `docs/project-development-sop-20261106` 的基线为远端 release `d2d30a7af2f9c570254c9e830135b778cb1faeaa`（1.1.0）；`origin/main=029bf1c2`（1.0.7）。本机候选 `b9d60cf1` 后另有 `76f794fe` 图库入口构建候选，均不能称已发布。原始孤立快照无 Git，其历史 JSON 不作为提交证据。已核对清单所有显式路径和文件名在真实分支的存在性；但未逐函数证明候选行为已全部同步，涉及候选合同应由实际源码变更和交付范围确认。
- 下文 `feat/<模块>/<事项>`、`fix/<模块>/<事项>` 都是**建议命名模板，不是已存在分支**。实际任务应从当前授权基线建立短期分支，不维持一组相互漂移的永久模块分支。
- “测试起点”是实际存在的文件/模块，不表示本次运行通过。此次仅静态盘点，未安装依赖、执行测试、启动 GUI、操作游戏或构建发布。
- 路径缩写：`Q/` = `experiments/qt_quick/`；`A/` = `app/`；`T/` = `tests/`。`Q/test_*.py` 与 `T/test_*.py` 属于不同测试目录，运行时须同时纳入模块路径。

## 调度速查

以下是维护责任域，不是 17 条需要立即创建的长期分支；具体任务只建对应短分支。每项详细入口、依赖、风险和验收见后文相同编号。云端均可做源码/合同工作，原生栏是不能用云端离线结果替代的部分。

| 编号 | 持续维护责任 | 建议任务分支前缀 | 本机 Windows 关键验收 |
| --- | --- | --- | --- |
| 01 | 应用启动、单实例、后台与退出 | `fix/client-lifecycle/` | IPC 唤回、异常清理、完整进程退出 |
| 02 | Qt/Tk 页面、控件、主题、引导 | `feat/ui/`、`fix/tk-ui/` | 真实窗口、DPI、焦点和像素 |
| 03 | 原生输入、窗口绑定、控制布局 | `fix/native-input/`、`feat/control-layout/` | hook、消息循环、前台、输入穿透 |
| 04 | 客户区采集、准备和捕获排除 | `fix/capture/` | full-client、窗口尺寸/DPI、原生异常 |
| 05 | 身份、正侧门、相似组与手选 | `fix/map-identity/`、`feat/map-picker/` | 获准的真实帧和用户选择链 |
| 06 | 贴合、重开、楼层与提交状态机 | `fix/alignment/`、`fix/floor-state/` | 当前帧显示、关闭撤销、可见端点 |
| 07 | 赛季/模式 provider 与实验隔离 | `feat/season-provider/`、`experiment/s1/` | 实例身份及各能力实际边界 |
| 08 | 定点/贴合/预览、作者图与透明度 | `fix/display/`、`feat/author-display/` | 分层显隐、显示桥和真实像素 |
| 09 | 自定义路线编辑与导入导出 | `feat/route-editor/` | 文件选择器、保存重开、编辑交互 |
| 10 | 账号、路线广场与投稿 | `feat/route-community/`、`fix/account/` | DPAPI；真实服务另按本次授权 |
| 11 | 下载、导入/升级/删除与恢复事务 | `fix/resource-transaction/`、`feat/resource-download/` | helper、文件锁、恢复与数据保留 |
| 12 | 资源目录、图库浏览与缓存 | `fix/resource-browser/`、`perf/resource-browser/` | 大图库交互、刷新后真实资源 |
| 13 | 设置、路径、用户数据兼容 | `fix/settings/` | 实际落盘/重载、安装升级保留 |
| 14 | 开箱等主动输入辅助 | `fix/chest-assist/` | 前台/绑定/停止；游戏操作单列 |
| 15 | 诊断、局部样本、隐私和容量 | `fix/diagnostics/` | 原生文件身份、轮换、退出排空 |
| 16 | 地图离线生产、来源与资源合同 | `feat/map-tools/`、`resource/<赛季>/` | 与产品包实际 provider 对接 |
| 17 | 构建、身份、安装器、更新公告、CI | `build/windows/`、`ci/` | Setup、PE/UAC、安装/升级/卸载 |

## 一、总体依赖和最高风险接缝

### 真实运行链

Qt 包入口为 `tools/qt_client_entry.py:run` → 包身份校验/启动错误边界 → `Q/client.py:main` → `QGuiApplication` → `SingleInstanceGuard` → `QtClient` 服务组合。目录虽叫 experiments，不能误写为“仅概念验证”；Qt 已有真实生产/私有实例构建配置。`Q/main.py`、`Main.qml`、replay 工具是另一实验/回放链，不应替代 Client 主入口验收。

`QtClient` 组合 `SettingsService`、`ResourceService`、`PackageManagerService`、`SessionService`、`LiveRuntime`、`ClientCoordinator`、原生输入、采集、窗口、显示、账号广场及故障服务。识别/贴合经 `client_engine.py`、`client_providers.py` 调用共享 `app/` 模块，不是把旧 Tk 主程序加载进 Qt。

Tk 主入口 `A/identityv_daily_assistant.py` 仍存在，含 UI、业务状态、后台任务及落盘联动；配套 `desktop_*`、资源页、编辑器。实测文本规模：Tk 主文件 40268 行，Qt 1.1.0 `client_live_runtime.py` 5623 行、`client_coordinator.py` 2612 行（本机候选分别为 5862/2643 行）。它们是协作冲突风险，不是建议马上重构的依据。

### 必须单写者协调的文件/合同

1. `A/identityv_daily_assistant.py`、`Q/client.py`、`Q/client_coordinator.py`、`Q/client_live_runtime.py`：多个模块都经过这里；指定一个接线负责人，其他分支先提交服务内改动与明确接口，不各自整段替换组合代码。
2. `Q/client_engine.py`、`A/alignment_core_v2.py`、`A/map_identity_engine.py`：身份、姿态、结果提交共同边界。更换作者图不得改变结构身份；历史证据不得给当前帧显示授权。
3. `Q/client_settings.py`、`Q/client_paths.py`、`Q/client_providers.py`、`A/season_context.py`、资源索引/锁清单：键迁移、实例隔离、资源身份和能力判断是共享协议，跨分支改动需先对齐 schema 和兼容策略。
4. `A/resource_transaction.py`、`star_map_import.py`、`map_package_upgrade.py`、包收据/helper：导入、启动修复、删除均触碰相同文件所有权与依赖，禁止各做一套恢复逻辑。
5. `tools/build_qt_client.py`、spec/iss、`package_profiles.py`、`packaged_product_identity.py`、`version.json`：由集成/发布负责人最终统一处理；模块开发不能顺带提升版本或改变 AppId。

## 二、模块责任清单

### 01 Qt 应用外壳、启动、单实例与退出

- 入口/文件：`tools/qt_client_entry.py`；`Q/client.py`、`Client.qml`、`ClientBoot.qml`、`client_single_instance.py`、`client_lifecycle.py`、`client_startup_cleanup.py`、`client_process_identity.py`、`client_windows.py`；`A/qt_startup_splash.py`【本机 1.1.1 候选/未同步】、`qt_startup_diagnostics.py`。
- 依赖/接缝：QML 服务注册、工作目录、产品实例、后台 worker/helper、已接受写入、输入线程、窗口恢复 IPC。单实例仲裁先于引擎/输入/worker；错误中途创建也必须清理。
- 共写风险：`client.py`、`client_coordinator.py` 由接线负责人合并；后台隐藏与真实退出不可混为一个布尔值。
- 建议分支：`fix/client-lifecycle/<事项>`。
- 测试起点：`Q/test_client_single_instance.py`、`test_client_shutdown.py`、`test_client_partial_cleanup.py`、`test_client_process_identity.py`；`T/test_qt_entry_startup_failure.py`、`test_qt_startup_failures.py`。
- 验收：启动失败后再启动、重复启动唤回、等待游戏仍可使用管理快捷键；X 的后台/退出/取消分别验证；有任务/有错误时退出须证明自有进程与子进程正常结束，不用隐藏或 quit 信号冒充。

### 02 Qt/Tk 页面、控件、主题与引导

- Qt：`Q/ClientHome.qml`、`ClientSettings.qml`、`ClientHelp.qml`、`ClientTutorial.qml`、`ClientOnboarding.qml`、`ClientControls.qml`、AppButton/CheckBox/Slider/Switch、`ClientPalette.qml`；`client_theme.py`、`client_tutorial.py`、`client_onboarding.py`、`client_ui_feedback.py`。
- Tk：`A/desktop_main_ui.py`、`desktop_controls.py`、`desktop_theme.py`、`desktop_rounded.py`、`desktop_icons.py`、`desktop_lucide.py`、`desktop_toast.py`、`desktop_onboarding.py`、`desktop_tutorial.py`、`support_window.py`，由 Tk 主入口接线。
- 依赖/风险：页面可见性不是后台推进条件；稳定页面/卡片 ID、缓存、焦点/滚动和信号去重。普通状态按钮不能整页刷新。UI 字段与状态写者跨分支同步。
- 建议分支：`feat/ui/<事项>`，Tk 专项可用 `fix/tk-ui/<事项>`。
- 测试：`Q/test_client_theme.py`、`test_client_tutorial.py`、`test_client_onboarding.py`、`test_client_controls_visibility.py`、`test_settings_accessibility.py`；`T/test_control_style_idempotence.py`、`test_desktop_animation_scheduling.py`、`test_desktop_scroll_allocation.py`、`test_map_browser_page_lifecycle.py`。
- 验收：同页重复点击、来回导航、隐藏恢复、普通/大窗口/DPI、鼠标/键盘焦点、图片首次显示；源码或离屏 QML 检查不等于真实像素。

### 03 原生输入、游戏窗口绑定、焦点与控制布局

- Qt：`Q/client_input.py`、`client_native_keys.py`、`client_native_mouse.py`、`client_window_binding.py`、`client_game_focus.py`、`client_map_input.py`、`client_control_layout.py`、`client_control_snap.py`、`client_floor_region.py`。
- 共享：`A/hotkey_bindings.py`、`hotkey_keys.py`、`game_window_focus.py`、`control_layout.py`、`control_layout_editor.py`、`game_control_view.py`、`map_close_control.py`、`floor_control_region.py`、`s0_vertical_controls.py`。
- 依赖/风险：严格窗口身份和前台校验、输入 epoch/序列、按键修饰符/布局、模式/赛季、原生消息循环与退出注销。应用内 QShortcut 不能替代后台全局输入；不得吞键或把别的应用输入计为地图意图。
- 建议分支：`fix/native-input/<事项>`；控制布局独立 `feat/control-layout/<事项>`。
- 测试：`Q/test_client_input.py`、`test_client_input_event_freshness.py`、`test_client_input_recovery.py`、`test_client_native_key_state.py`、`test_client_native_mouse.py`、`test_hotkey_layout_compat.py`、`test_client_modifier_shortcuts.py`、`test_client_game_focus.py`、`test_client_game_floor_input.py`。
- 验收：后台/前台、游戏重启重新绑定、按住修饰键、重复输入、输入穿透、鼠标地图模式、失焦后恢复；离屏和注入事件不能替代 Win32 真实键鼠链。

### 04 游戏客户区采集、准备与捕获排除

- 文件：`Q/client_capture.py`、`client_capture_exclusion.py`、`client_preparation_diagnostics.py`；`A/client_region_capture.py`、`client_space_geometry.py`、`identityv_live_mvp.py`、`map_open_preparation.py`、`map_ready_window.py`、`map_interface_observation.py`、`map_observation.py`。
- 依赖/风险：本次原生窗口上下文、full-client 意图、屏幕/DPI 坐标、窗口/采集代次、前台验证；排除自身 Qt 窗口，保存原始帧与异常尺寸。UI 初次发现的宽高不能冻结成后续 ROI。
- 建议分支：`fix/capture/<事项>`。
- 测试：`Q/test_client_capture_full.py`、`test_client_capture_compat.py`、`test_client_capture_exclusion.py`、`test_client_window_feedback.py`、`test_client_recapture_focus.py`；`T/test_capture_start_failure.py`、`test_capture_environment_observation.py`、`test_capture_preparation_privacy.py`。
- 验收：改变窗口大小、异常尺寸、准备超时/过渡帧、采集中退出、前台失效、再捕获；失败不可回退上一帧去伪造本次证据。

### 05 地图身份、正门/侧门、已复核相似组与手选

- 共享：`A/map_identity_engine.py`、`runtime_map_recognition.py`、`difficult_native_identity.py`、`difficult_entry_identity.py`、`difficult_side_entry_identity.py`、`front_gate_observation.py`、`front_door_recognition.py`、`side_entry_search.py`、`side_entry_geometry.py`、`native_similarity_groups.py`、`native_sibling_transaction.py`、`manual_map_selection.py`、`manual_map_picker_core.py`。
- Qt：`Q/client_engine.py`、`client_group_registry.py`、`client_group_member_choice.py`、`client_manual_map.py`、`client_manual_map_service.py`、`ClientMapPicker.qml`、`ClientRecognition.qml`。
- 依赖/风险：资源登记→候选结构证据→组身份/暂定成员→当前帧唯一验证→提交；动态候选和局部兼容不能扩大手选成员表。登记表具体成员须核当前源码与授权，不将 MIGRATION 历史文本当永久授权。
- 建议分支：`fix/map-identity/<事项>`；手选 UI 仅改入口可 `feat/map-picker/<事项>`，仍验身份接缝。
- 测试：`T/test_native_similarity_runtime.py`、`test_native_similarity_resources.py`、`test_front_gate_observation.py`、`test_side_entry_search.py`、`test_manual_map_selection.py`、`test_unconfirmed_identity_recovery.py`；`Q/test_client_registered_groups.py`、`test_client_group_registry.py`、`test_client_similarity_protocol.py`、`test_client_manual_map_integration.py`。
- 验收：独立图不被兼容集合吞并、组内手选/换层保持、正侧门隔离、唯一结果失败回竞争、资源缺口明确显示；不得以例子/合成测试补生产名单。

### 06 首贴、重开贴合、楼层观测、状态机与提交

- 文件：`A/alignment_core_v2.py`、`alignment_pose_transaction.py`、`alignment_observation.py`、`alignment_structure_dispatch.py`、`fast_structure_alignment.py`、`floor_contract.py`、`floor_observation_policy.py`、`round_layer_group.py`、`round_layer_group_commit.py`、`session_state_machine.py`、`b1_relocalization.py`、`shared_structure_context.py`；`Q/client_live_runtime.py`、`client_session.py`、`client_engine.py`、`client_known_floor_recovery.py`、`client_coordinator.py`。
- 依赖/风险：输入/准备→worker→身份/几何验证→round-pose/core→实际可见像素。地图/楼层/帧/请求代次/赛季/资源身份及取消必须同一提交协议；噩梦与困难共核心，差异走 provider 与坐标契约。
- 建议分支：`fix/alignment/<事项>`、楼层问题 `fix/floor-state/<事项>`；同一状态机只能有明确主写者。
- 测试：`T/test_queued_first_pose_delivery.py`、`test_queued_delivery_core_contract.py`、`test_reopen_lifecycle.py`、`test_reopen_root_validation.py`、`test_floor_contract.py`、`test_floor_observation_evidence.py`、`test_shared_recovery_delivery.py`、`test_committed_floor_bridge.py`；`Q/test_client_map_open_recovery.py`、`test_client_map_recovery_late_error.py`、`test_client_material_identity.py`、`test_client_material_handoff.py`。
- 验收：首贴和已锁图后的每次重开分开；成功/失败/取消/重复/迟到、换层/换图/关图/结束局反例；500ms/300ms 只是目标，报告计算与真实像素端到端 p50/p95/max、样本数/未显示/有真值错层/未知，不删除失败样本。

### 07 赛季/模式 provider、S0/S1 资源与实验身份

- 文件：`A/season_catalog.py`、`season_context.py`、`season_map_resources.py`、`s0_legacy_base_releases.py`、`s1_native_resources.py`、`s1_native_display.py`、`s1_floor_observation.py`、`s1_map_ui_geometry.py`、`s1_structure_selection.py`、`s1_common_coordinate_filter.py`、`s1_pose_prior.py`、`s1_candidate_purpose.py`、`s1_private_experiment.py`、`s1_stream_priority.py`、`s1_reviewed_base_releases.py`；`Q/client_providers.py`、`s1_private_resources.py`、`SeasonSelector.qml`；`s1-private-assets/`。
- 依赖/风险：typed metadata、每季支持模式与楼层、资源实例、包身份、候选池 revision、独立证据 lane。普通关图撤销像素不等于可以把历史证据当当前验证；实验锁不能宣称已正式校准。
- 建议分支：`feat/season-provider/<事项>`、私有实验 `experiment/s1/<事项>`，必须保持产品身份/安装目录隔离。
- 测试：`T/test_season_provider_boundary.py`、`test_season_resources.py`、`test_s1_runtime_contract.py`、`test_s1_async_evidence.py`、`test_s1_pool_revision.py`、`test_s1_candidate_runtime.py`、`test_s1_production_candidate_purpose.py`、`test_s1_private_packaging.py`；`Q/test_client_season_preparation.py`、`test_s1_resources_changed_chain.py`【本机 1.1.1 候选/未同步】、`test_client_s1_floor_observation.py`。
- 验收：不同赛季/模式资源隔离与不支持能力；S0 与 S1 不能一概按三层建模；正式/实验/手选分支与标签可辨。具体交付是否要求识别矩阵须听当前用户指令，不能以通用 SOP 覆盖私有交付明确排除的门槛。

### 08 定点/贴合/预览显示、作者图与透明度

- 共享：`A/optional_author_displays.py`、`author_display_policy.py`、`author_route_catalog.py`、`author_structure_resources.py`、`base_map_display.py`、`base_display_integration.py`、`generic_author_base.py`【本机 1.1.1 候选/未同步】、`transparent_display_resources.py`、`nightmare_author_coordinate_adapter.py`、`s1_author_displays.py`、`idvb0059_display.py`、`reference_position_editor.py`、`opacity_controls.py`、`map_pointer_policy.py`。
- Qt：`Q/client_reference_presentation.py`、`client_preview.py`、`client_custom_display.py`、`client_route_reprojection.py`、`client_route_preferences.py`、`ClientReference.qml`、`ClientAligned.qml`、`ClientPreview.qml`。
- 依赖/风险：选择偏好→实际每层来源→结构坐标/显示桥→缓存→像素。换作者/自定义路线只影响显示；结构回退层也必须有正确来源。图库预览须独立可交互，定点/贴合显隐互不阻断。
- 建议分支：`fix/display/<事项>`、`feat/author-display/<事项>`。
- 测试：`T/test_route_selection_controller.py`、`test_route_selection_regressions.py`、`test_retained_display_provenance.py`、`test_display_context_snapshot.py`、`test_independent_visibility_runtime.py`、`test_runtime_opacity_controls.py`；`Q/test_client_aligned_transform.py`、`test_client_reference_presentation.py`、`test_client_preview.py`、`test_client_custom_display.py`。
- 验收：换作者不漂移身份/姿态/楼层，实际显示源与缓存一致，关闭后迟到结果不复活，隐藏恢复，透明图/不同 DPI/地图边缘真实像素。

### 09 自定义路线编辑、图库、导入导出与编辑基础包

- 文件：`A/custom_maps.py`、`custom_map_editor.py`、`custom_default_gallery.json`；`Q/client_custom_gallery.py`、`ClientCustomGallery.qml`、`ClientGallery.qml`、`RoutePointNumbersSwitch.qml`；`tools/prepare_qt_editor_base.py`、`draw_custom_routes.py`、`tools/maps/build_custom_map_package.py`、`extend_custom_map_package.py`。
- 依赖/风险：基础图坐标、道路连通/门、路线点顺序与颜色、schema、图包版本与显示重投影。编辑的路线不能偷改识别资源。
- 建议分支：`feat/route-editor/<事项>`。
- 测试：`T/test_custom_maps.py`、`test_custom_map_editor.py`、`test_custom_map_reachability.py`、`test_custom_route_integration.py`、`test_editor_door_display.py`；`Q/test_client_custom_gallery.py`、`test_client_gallery_export_dialog.py`、`test_client_route_point_numbers.py`、`test_client_route_reprojection.py`；`tools/test_prepare_qt_editor_base.py`、`tools/maps/test_build_custom_map_package.py`。
- 验收：真实文件选择器、取消/重复导入、编辑保存和重新打开、楼层/地图切换、导出再导入、旧基础包升级保留用户图集；服务调用成功不是 UI 入口通过。

### 10 账号、路线广场下载与投稿

- 文件：`A/account_service.py`、`local_tokens.py`、`route_plaza.py`、`route_submission.py`；`Q/client_route_plaza.py`、`ClientAccount.qml`、`ClientRoutePlaza.qml`、`ClientRouteSubmission.qml`。
- 依赖/风险：公共列表、登录后短链下载、ZIP/schema、Android 网格→本地坐标重规划、账号刷新/登出代次、投稿幂等与失败状态。Windows DPAPI 保存刷新令牌；密码不落盘；其他系统仅会话；禁止令牌跨重定向泄漏。
- 建议分支：`feat/route-community/<事项>`；认证修复可 `fix/account/<事项>`。
- 测试：`T/test_account_concurrency.py`、`test_route_plaza.py`、`test_route_plaza_redirect.py`、`test_route_submission.py`；`Q/test_client_account_lifecycle.py`、`test_client_route_plaza.py`、`test_client_route_submission.py`。
- 验收：并发刷新、登出后旧响应、401/403/429、下架/失效下载链接、坏包/过大包、投稿失败重试和是否实际已提交；离线假服务与真实外部发送分开，真实账号与投稿仍按当前授权。

### 11 在线资源、导入/升级/删除事务、收据和恢复

- 共享：`A/star_map_download.py`、`star_map_import.py`、`star_map_import_helper.py`、`star_map_recovery_helper.py`、`resource_transaction.py`、`map_package_upgrade.py`、`map_package_removal.py`、`map_package_recovery_plan.py`、`managed_base_resources.py`、`generic_author_package.py`【本机 1.1.1 候选/未同步】、`s1_author_package.py`、`author_package_management.py`、`legacy_author_inventory.py`、`legacy_author_receipt_contracts.py`、`bundled_author_resources.py`。
- Qt：`Q/client_downloads.py`、`client_import_handoff.py`、`client_package_manager.py`、`client_resource_startup.py`、`client_resource_startup_recovery.py`、`client_resource_recovery.py`、`ClientOnlinePackages.qml`。
- 依赖/风险：下载校验→事务→收据→provider→列表/显示刷新→任务/cache 失效；session lease、helper IPC、路径/哈希/所有权/依赖、journal 恢复。requiresFiles 不等于拥有权；损坏/缺失依赖失败关闭；旧收据保留启动不授予迁移/删除/作者显示权。
- 建议分支：`fix/resource-transaction/<事项>`、下载独立 `feat/resource-download/<事项>`。
- 测试：`T/test_map_package_upgrade.py`、`test_map_package_removal.py`、`test_resource_transaction.py`、`test_package_dependency_upgrade.py`、`test_receipt_dependency_contract.py`、`test_pre_dependency_author_receipts.py`、`test_legacy_receipt_startup_retention.py`【本机 1.1.1 候选/未同步】、`test_download_cache_recovery.py`、`test_star_map_recovery_helper.py`；`Q/test_client_import_upgrade_parity.py`、`test_client_resource_recovery_lease.py`、`test_client_transaction_retirement.py`、`test_client_downloads.py`。
- 验收：普通新增、替换确认、重复包、同字节不同依赖拒绝、损坏收据/外链/越界、取消/断电/中途退出恢复、旧资源保护、成功后实际列表和当前显示。清理失败保留恢复记录并阻止新操作，不能误报成功。

### 12 资源目录/浏览、缓存与上下文刷新

- 文件：`A/map_resource_model.py`、`map_resource_context.py`、`map_resource_browser.py`、`map_resource_overview.py`、`map_resources_page.py`、`native_resource_validation.py`、`resource_validation_lane.py`；`Q/backend.py`、`client_resources.py`、`client_resource_errors.py`、`ClientResources.qml`、`ClientGallery.qml`。
- 依赖/风险：安装资源、作者登记、赛季/模式、预览图片、实际可用能力；资源页浏览模式与对局模式锁分离；资源变化按身份失效，状态变化不全库重载。
- 建议分支：`fix/resource-browser/<事项>`。
- 测试：`T/test_map_resource_context.py`、`test_map_resource_action_flow.py`、`test_map_resources_page.py`、`test_structure_preview_corruption.py`、`test_preview_work_lifecycle.py`；`Q/test_client_gallery_resource_refresh.py`、`test_client_resource_readers.py`、`test_client_resource_feedback.py`、`test_client_resources.py`。
- 验收：坏图/缺图/不支持能力、快速切页、导入或删除后刷新、隐藏页面任务有界、保留滚动/焦点/选择，迟到图片必须核对上下文。

### 13 设置持久化、运行路径和用户数据保留

- 文件：`Q/client_settings.py`、`client_paths.py`、`client_route_preferences.py`、`client_chest_preferences.py`、`client_lifecycle.py`、`ClientSettings.qml`；共享 `A/season_context.py`、`hotkey_bindings.py`、`control_layout.py`、Tk 主程序设置路径。
- 依赖/风险：设置验证/默认值/旧键迁移→通知→会话→真实控件；保存排空、原子写、实例路径隔离；旧设置显式 false/非法值/读失败不能错误打开自动失败截图。
- 建议分支：`fix/settings/<事项>`。
- 测试：`Q/test_client_settings.py`、`test_client_paths.py`、`test_season_settings.py`、`test_s1_author_settings_chain.py`、`test_hotkey_layout_compat.py`；`T/test_settings_independent_opacity.py`、`test_qt_upgrade_preservation.py`、`test_default_failure_evidence.py`。
- 验收：首次启动/升级/损坏文件、快速连续保存后退出、账号/自定义图集/旧配置保留、临时 UI 值与已落盘值一致，切模式不串作者/透明度/快捷键。

### 14 自动开箱等主动输入辅助

- 文件：`A/chest_auto_tap.py`；`Q/client_chest_auto_tap.py`、`client_chest_preferences.py`、`ClientChestAutoTap.qml`。
- 依赖/风险：明确启用意图、绑定窗口/前台、快捷键冲突、赛季/模式与退出。不可因为配置迁移或界面隐藏继续向错误窗口发输入。
- 建议分支：`fix/chest-assist/<事项>`。
- 测试：`T/test_chest_auto_tap.py`、`Q/test_client_chest_auto_tap.py`、`test_client_chest_intent.py`；工作流 `qt-chest-intent-windows.yml`。
- 验收：默认/启用/取消、冲突修复、失焦/换绑定停止、结束局/退出停止；真实输入验证单独授权和记录。

### 15 诊断、失败样本、隐私与本地容量

- 共享：`A/async_event_log.py`、`diagnostic_schema.py`、`diagnostic_identity.py`、`diagnostic_privacy.py`、`diagnostic_storage.py`、`diagnostic_evidence.py`、`diagnostic_export.py`、`failure_sampling.py`、`calibration_memory.py`、`recognition_diagnostics.py`、`runtime_environment_diagnostics.py`、`runtime_timeline.py`、`hang_diagnostics.py`。
- Qt：`Q/client_lifecycle_trace.py`、`client_failure_samples.py`、`client_input_diagnostics.py`、`client_preparation_diagnostics.py`、`client_fit_reporting.py`、`client_problems.py`、`client_problem_history.py`、`ClientProblemsPanel.qml`。
- 依赖/风险：inputSequence/openingId/attemptId 与真实捕获身份，局部图 ROI、原生句柄身份、轮换、导出会话关联、后台写队列；诊断不得改变识别判定或阻塞退出。局部图仍可能含昵称/聊天，不承诺匿名，不自动上传。
- 建议分支：`fix/diagnostics/<事项>`。
- 测试：`T/test_diagnostic_*.py`、`test_async_event_log.py`、`test_automatic_evidence_rotation.py`、`test_automatic_failure_sample_admission.py`、`test_failure_capture_ownership.py`、`test_failure_capture_terminals.py`、`test_map_attempt_correlation.py`、`test_storage_retention.py`；`Q/test_diagnostic_noninterference.py`、`test_diagnostic_log_budget.py`、`test_client_joint_diagnostic_schema.py`、`test_field_failure_samples.py`。
- 验收：缺帧只记原因、不回退旧帧；图片与 journal 都提交后才消耗成功冷却；链接/替换/多链接/损坏 journal 失败关闭；Windows 同句柄创建/校验/删除，Linux skip 不能当原生通过。日志 8 份、automatic-v2 每模式 6 张/12MiB/7 天等上限以当前源码与规范为准，用户接受/拒绝贴合记录不自动清理。

### 16 地图离线生产、来源与资源合同

- 工具：`tools/maps/extract/*.cjs`、`normalize_increment_catalog.cjs`、`render_native_increment.cjs`、`render_native_entry_rooms.cjs`、`render-candidate-structures.cjs`；`assemble_structure_resources.py`、`prepare_fast_structures.py`、`prepare_shared_fast_structures.py`、`native24/`、`build_native_increment.py`、`build_native_similarity_groups.py`、`build_similarity_conditions.py`、`scan_similarity_catalog.py`、`authors/`、`optimize_map_packages.py`、自定义地图构建/扩展工具。
- 资源/文档：`references/`、`resources/0B721D65-public1/manifest.json` 与 SHA256SUMS、`examples/resource-root/`、`docs/resources*.json`、`docs/map-tool-provenance.json`、`docs/map-processing.md`、`docs/resource-release.md`、`skills/update-helper-maps/`。
- 依赖/风险：合法来源→提取→结构/坐标/门/索引→运行时消费者→发布清单。离线相似扫描不是用户复核成员登记；图片可用不等于坐标/结构版本兼容。Node .cjs 与 Python 工具分开记录版本；SciPy 仅地图工具依赖，不加入运行时。
- 建议分支：`feat/map-tools/<事项>`、授权资源更新 `resource/<赛季>/<事项>`。
- 测试：`tools/maps/test_public_tools.py`、`test_build_custom_map_package.py`、`test_extend_custom_map_package.py`、`test_incremental_editor_connections.py`、`test_optimize_map_packages.py`、`tools/maps/extract/test-extraction.cjs`；`T/test_native_increment_contract.py`、`test_similarity_condition_builder.py`、`test_native_resource_validation.py`、`test_s1_production_base_resources.py`。
- 验收：来源/授权、全量清单哈希、缺绑定/缺门明确失败、消费者使用实际资源验证、资源版本与程序 commit 分别记录；不得把游戏原包、未授权素材和私人捕获混入源码。

### 17 构建、产品身份、安装器、公告与 CI

- 文件：`tools/build_qt_client.py`、`rebuild_qt_installer.py`、`qt-client.spec`、`qt-import-helper.spec`、`qt-recovery-helper.spec`、`qt-client.iss`、`verify_admin_manifest.py`、`package_profiles.py`、`packaged_resource_contract.py`、`runtime_exclusions.py`、`s1-production-release-lock.json`；`A/package_identity.py`、`packaged_product_identity.py`、`release_identity.py`、`version.json`。
- 旧 Tk 构建：`tools/build.py` + `xiaochao-assistant.spec` 明示为 development executable skeleton，不是与完整 Qt Setup 等价交付。
- 更新/公告：`A/update_service.py`、`Q/client_info.py`、`tools/preview_windows_notice.py`、`publish_windows_notice.py`、`docs/windows-update-notice.md`、`docs/windows-build-record.example.json`。当前 update_service 读官方 manifest/history，打开官方网盘页面；不自行下载安装，不能写成已实现在线自更新。
- 依赖/风险：远端干净 commit + 资源清单/锁哈希 + 包身份 + PyInstaller/Qt allowlist + Inno Setup + PE manifest。主 EXE `uac_admin=True` 和产物 RT_MANIFEST 双核验，helper asInvoker；不要借计划任务绕过 UAC。
- 建议分支：`build/windows/<事项>`、`ci/<事项>`；集成用 `integration/<目标>`，发布分支是否建立由仓库实际流程决定。
- 测试：`T/test_admin_manifest.py`、`test_package_profiles.py`、`test_packaged_resource_contract.py`、`test_packaged_product_identity.py`、`test_qt_import_helper_packaging.py`、`test_qt_installer_directory.py`、`test_qt_installer_reuse_guard.py`、`test_qt_upgrade_preservation.py`、`test_preview_windows_notice.py`、`test_publish_windows_notice.py`、`test_update_service.py`。
- 验收：同一最终源码 commit 定向联合检查后构建；Setup 字节数/SHA256、显示/数值版本、指纹、资源锁/实际内置范围、未测项。安装、真实 GUI/游戏、发布各独立状态，不由构建成功推导。

## 三、依赖与测试执行口径

已核对锁定依赖：运行时 `requirements.txt` 为 numpy 2.2.6、opencv-python-headless 4.12.0.88、Pillow 12.3.0；Qt `Q/requirements.txt` 额外锁 PySide6-Essentials 6.11.1、shiboken6 6.11.1、psutil 7.2.2；`Q/requirements-client.txt` 包含 Qt 依赖并补 OpenCV。构建 `requirements-build.txt` 锁 PyInstaller 6.22.2 / hooks-contrib 2026.7；`tools/requirements-qt-build.txt` 组合构建/Qt 并锁 pefile 2024.8.26。地图工具 `tools/maps/requirements-maps.txt` 加 scipy 1.15.3。不要将这些未来更新固定成永久版本要求，更新时修改对应清单并重验。

已有 Windows CI 共 15 个 YAML，主题含 diagnostics、gallery recovery、chest intent、controls visibility、floor ready、hotkey layout、manual picker、map intent、modifier shortcuts、next integration、recapture focus、resource feedback、route plaza、transient fit、window feedback。多数工作流只监听历史特定分支加 workflow_dispatch；部分含 main PR 触发，不是“任意新分支自动全套 CI”。`qt-next-integration-windows.yml` 当前列 features/core/diagnostics/packaging/tutorials/map-defaults/season-ui/main-baseline/released-baseline 九组，基线比较不能冒充候选通过。

可复用的执行形式（需在实际授权的 Windows checkout 验证依赖后使用，以下未执行）：

```powershell
$env:PYTHONPATH = 'app;tests;tools;tools/maps;experiments/qt_quick'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_QUICK_BACKEND = 'software'
$env:PYTHONUTF8 = '1'
python -B -X utf8 -m unittest -v test_client_session test_client_shutdown test_queued_delivery_core_contract
```

按变更选择模块，勿为小修固定全库。`test_runtime_environment_diagnostics.py` 自带 FunctionTestCase runner，现有 CI 特别用 `python tests/test_runtime_environment_diagnostics.py`，普通 unittest discovery 会得到零测试。需要发现全部 Qt 测试时不能只 discover `tests/`。离屏 QML、合成 fixture、保存帧回放、Windows 原生合同、真实游戏端到端分别写明。运行时记录每条实际命令/退出码/skip 原因/输入来源，不用测试文件存在证明覆盖完成。

## 四、建议的并行分工与合并顺序

1. 各任务先列“入口、状态写者、缓存/页面、后台任务、最终消费者”，确认与其他分支的共享文件。
2. 可平行：UI 纯表现、离线工具、独立诊断 schema 消费者、单个资源事务测试；但依赖 schema/身份/生命周期改变时先串行定协议。
3. 接口先合并：provider/资源身份、状态机事件、设置键或包 schema。随后服务实现，再前端接线，最后打包/版本/发布记录。不能靠最后解决文本冲突代替语义整合。
4. 同一核心文件有多个任务时，用一个集成写者接收小差异；不要多人分别复制整个 Tk/Qt runtime。各 PR 写清原基线、依赖 PR、受影响模式和未测项。
5. 合并后在最终远端同一 commit 运行跨接缝代表回归并构建。纯文档与模块导航不要求全库测试；交付范围按当前授权，任何实际发布另有明确状态。

## 五、此次发现的文档和证据风险

- `experiments/qt_quick` 名称/文件头“opt-in private client”与已有 production 构建并存，SOP 要按入口和包身份描述，不能简单写“Qt 尚未主用”或“Tk 已移除”。
- 资源是单独合同，快照内存在示例/公共资源元数据，不代表拥有所有生产图片/NPZ 或可直接构建。`.orig`、`__pycache__` 与历史快照 JSON 也不代表当前可发布源。
- `MIGRATION.md` 保存多个历史复核/阻塞结论，根开发文档又有私有候选交付记录。当前是否解除阻塞、已发布版本及最终包要另取当前提交/产物证据，不凭文档时间顺序推断。
- CI 有不少历史分支过滤；新分支需检查会否触发真实所需套件。此快照未查询 Actions 结果，所有“测试起点”均为待执行建议。
- 单元测试、源代码复核、安装器生成、包装启动、安装实测、真实游戏、线上公告是七种不同证据。SOP 应给每一项独立状态栏，避免把一个绿灯推广为整个模块已验收。


## 六、已存在的远端分支导航（仅按名称关联，不代表活跃或完成）

来源：在真实 Git 目录执行 `git for-each-ref --format='%(refname:short)' refs/remotes`，读到 119 条引用（118 条远端分支加 origin/HEAD 展示）。下列名称真实存在；不据名称推断已合并、通过、发布或应继续在该分支开发。开始任务仍查最新 tip、提交内容、PR 和授权基线。

| 模块 | 实际相关远端分支（均省略 origin/） |
| --- | --- |
| 01 启动与生命周期 | `experiment/qt-quick-resource-lab`、`fix/runtime-freeze-090b560`、`fix/qt-test-install-directory` |
| 02 Qt/Tk UI | `feature/xingtu-ui-refresh`、`perf/ui-smoothness`、`codex/ui-page-refresh-performance`、`fix/rounded-refresh-p0`、`fix/ui-cold-transitions`、`claude/inapp-tutorial-4eace3` |
| 03 原生输入/布局 | `feature/control-panel-layout`、`feat/floor-control-region`、`fix/qt-hotkey-layout-compat`、`fix/qt-modifier-main-ci-20261102`、`fix/qt-map-intent-reset`、`fix/s0-vertical-only-20261105` |
| 04 采集/准备 | `codex/capture-present-integration`、`fix/map-window-shape-feedback`、`fix/qt-recapture-focus-flow`、`fix/foreground-return-multiframe` |
| 05 身份/相似组/手选 | `codex/similar-map-groups`、`codex/similarity-two-stage`、`codex/front-gate-group-display`、`codex/xiaochao-side-entry`、`feature/qt-manual-map-mode`、`fix/qt-manual-picker-takeover-20261102` |
| 06 贴合/楼层/提交 | `codex/alignment-queued-recovery-20261028`、`codex/import-and-queued-delivery-20261028`、`codex/reopen-lifecycle-20261028`、`codex/reopen-runtime-20261028`、`codex/f1-current-frame-recovery`、`fix/qt-floor-ready-policy` |
| 07 赛季与 S1 | `codex/s1-formal-release-1.0.17`、`feat/qt-next-modules`、`fix/s0-vertical-only-20261105`；未发现名称直接对应本机 1.1.1 generic-author/启动动画的已同步分支，不能补造 |
| 08 作者/定点/贴合显示 | `codex/multi-author-route-switch-20261028`、`codex/author-base-display-fallback`、`codex/fixed-map-position-editor`、`codex/pr76-display-recovery-20261103`、`fix/retained-display-alias`、`author/lianghapi-nightmare`、`feat/aichicu-difficult-author-display`、`feat/zhan10-nightmare-author-display` |
| 09 自定义路线编辑 | `codex/tilemap-custom-editor`、`feature/legacy-custom-maps`、`feature/readable-custom-maps`、`fix/custom-road-links`、`fix/custom-route-full-cells`、`feat/qt-route-room-numbers` |
| 10 账号/广场/投稿 | `feature/route-plaza`、`feat/qt-route-plaza-account-login`、`feat/qt-route-plaza-submit`、`fix/gallery-download-traffic` |
| 11 资源下载/事务/恢复 | `codex/author-import-without-restart-20261028`、`fix/owned-map-package-upgrades-20261027`、`fix/map-package-safe-recovery`、`fix/live-author-upgrade`、`fix/author-resource-recovery`、`codex/author-legacy-adoption`、`codex/installed-download-state-20261028` |
| 12 资源目录/浏览 | `feature/map-resources-page`、`fix/managed-packages-tk-anchor-20261029`、`fix/resource-unready-feedback`、`cloud/gallery-recovery-compat`、`fix/qt-bundled-map-defaults` |
| 13 设置/数据保留 | `fix/settings-resource-recovery`、`fix/qt-controls-hidden-preference`、`fix/bounded-runtime-storage` |
| 14 开箱辅助 | `feature/custom-maps-chest-tap`、`fix/qt-chest-explicit-intent`、`fix/chest-conflict-recovery` |
| 15 诊断/隐私 | `codex/diagnostic-evidence-20261029`、`codex/diagnostic-session-association-20261029`、`cloud/diagnostics-main-20261102`、`cloud/diagnostics-e3-e2-e1-f2-20261102`、`feat/default-failure-evidence`、`fix/transient-fit-reporting` |
| 16 地图生产工具 | `codex/public-map-extraction-tools`、`codex/map-maintenance-20261029`、`codex/side-preprocess-20261029`、`codex/editor-static-door-display`、`fix/native-staging-resource-grant` |
| 17 构建/发布/CI | `codex/release-1.1.0-20261106`、`release/1.0.5`、`release/qt-1.0.6-metadata`、`fix/default-admin-executable`、`fix/admin-spec-test-double`、`fix/qt-setup-version-fields`、`fix/qt-notice-build-record`、`feature/windows-notice-publisher`、`fix/update-check-retry-state`、`integration/qt-pr84-88-20261103` |

### 与 1.1.0 基线的边界修正

- `app/qt_startup_splash.py` 不在 release 基线。1.1.0 入口只有无 Qt 的启动失败边界，不能写成已经包含本机候选启动动画。
- `app/generic_author_base.py`、`app/generic_author_package.py` 不在 release 基线；跨来源作者通用机制按本机 1.1.1 候选/未同步描述，不把对应候选路径写为已发布结构。
- `tests/test_legacy_receipt_startup_retention.py` 与 `Q/test_s1_resources_changed_chain.py` 不在 release 基线；候选测试存在不证明 release 已覆盖该合同。
- 其他清单中完整文件名在 release 树可找到，不代表函数实现相同，也不代表快照中的所有行为已发布。本表以稳定模块责任为主，候选差异单独标注；执行时绑定实际完整提交，而非仅有目录名的快照。
