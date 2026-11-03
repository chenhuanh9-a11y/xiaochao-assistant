# 赛季与难度上下文

## 身份和兼容边界

`app/season_context.py` 是共享的赛季身份契约。`season` 取 `s0` / `s1`，`mode` 仍取 `nightmare` / `difficult`，不把赛季塞进旧难度字段。完整键为 `s0:nightmare` 等四种组合。识别、贴合和对局生命周期仍共用原算法。

没有 `gameSeason` 的旧设置读取为 S0，读取本身不重写文件。`contextRouteProfiles` 保存完整上下文的作者偏好。S0 可读取原 `modeRouteProfiles` / `routeProfileId`；S1 不可借用这些旧偏好。原自定义图库文件及未知设置字段保留。S0 学习目录与设置文件位置不变；S1 学习目录预留为 `work/seasons/s1/<mode>-memory`，不读取 S0 样本。

## 所有者与失效

- coordinator 接收运行上下文请求，settings 持久化后由 session 原子提交至 runtime。session 的 desired 与 applied 上下文分开；只有实际 runtime 相符才确认完成。
- 运行中的赛季/难度在 coordinator、session 和 runtime 均受对局锁保护。相同上下文重复选择幂等；相同难度跨赛季是完整切换，不是作者重投影。
- 图库有独立浏览上下文。浏览赛季不会改变运行赛季，也不受对局切换锁限制。主页预览只取 applied 运行上下文。
- worker 请求、回执、列表/预览键、手选上下文和资源缓存包含赛季。旧请求不能覆盖更新后的上下文；资源失效仍通过既有 generation / revision 机制撤销迟到结果。

## 当前资源能力

四组合都有明确身份和能力报告。S0 延用现有资源合同，并继续运行原结构/坐标/哈希验证；“已登记”不表示当前安装一定完整可用。

S1 两种难度均未登记真实地图资源，当前明确不可用。界面可选择以查看缺口，但不能开始识别，不加载 S0 结构、作者、门、地图坐标、学习样本或自定义路线。不以合成 fixture 充当生产地图。

旧 ZIP 只属于 S0。显式 S1 导入目标、S1 manifest 或未知包仍拒绝，不放宽 packageId、路径、依赖和 SHA256 白名单。

正式接入 S1 前需要有来源的结构地图、地图/楼层索引、门与坐标合同、逐层显示绑定、经过复核的相似组（如适用）及资源包哈希/版本声明；然后为独立根目录增加经过验证的资源登记。当前不能仅把 S1 标志改成 available 或指向 S0 目录。

## 验证边界

本次仅云端源码、合成 fixture 和 Qt offscreen 回归。Windows 安装、真实游戏捕获/快捷键、真实 S1 地图和可见像素验收尚未进行。未修改版本、未打包、未发布。

### 云端验证记录（2026-11-03）

环境：Python 3.12.14、PySide6 6.11.1、OpenCV 4.12.0，`QT_QPA_PLATFORM=offscreen`。使用临时目录、合成引擎/帧及只读资源 fixture；未连接真实游戏。

执行方式（仓库根目录，已安装上述依赖的 Python）：

```sh
PYTHONPATH=experiments/qt_quick:app QT_QPA_PLATFORM=offscreen python -m unittest test_season_settings
PYTHONPATH=experiments/qt_quick:app QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p 'test_season_*.py'
PYTHONPATH=tests:experiments/qt_quick:app QT_QPA_PLATFORM=offscreen python -m unittest test_map_package_upgrade test_live_author_import
```

第一组，逐模块运行的 Qt 回归（21 个模块、335 项：331 通过、1 跳过、3 项既有失败；包含 `test_season_settings` 的 7 项）：`test_client_settings`、`test_client_paths`、`test_client_session`、`test_client_engine`、`test_client_providers`、`test_client_info`、`test_client_coordinator`、`test_client_custom_display`、`test_client_resources`、`test_client_manual_map_integration`、`test_client_live_runtime`、`test_client_control_actions`、`test_client_manual_map`、`test_client_gallery_count`、`test_client_gallery_layout`、`test_client_route_preferences`、`test_client_custom_gallery`、`test_client_material_identity`、`test_client_reference_presentation`、`test_client_composition`、`test_season_settings`。

其余互不重复的独立分组：

- `tests/test_season_gallery.py`、`tests/test_season_resources.py`、`tests/test_season_session.py`：38 项全部通过（分别 12、14、12 项）。不包含第一组的 `test_season_settings`。
- `test_map_package_upgrade`、`test_live_author_import`：38 项，37 通过、1 跳过。
- `tests/test_map_resource_context.py`：11 项，10 通过、1 项既有失败。
- `test_client_control_stability`：9 项既有失败，单独运行，未计入第一组 335 项。

以上既有失败共 13 项（第一组 3 项、浏览资源 1 项、控件稳定性 9 项）。重复执行的验证批次不再累计；将四个赛季测试模块合并复核时是 45 项（7 + 38），并非额外新增 45 项。

与未改动基线 `9d7fa8024f1f5adf27fb664e2fcd1788d4dc883a` 在同环境逐模块对照，已知失败单列，不宣称全绿：

- `test_map_resource_context` 的 `test_escape_paths_are_rejected_before_file_stat`：Python 3.12 的 `Path.resolve()` 触发 `stat`，与该替身断言冲突。
- `test_client_live_runtime` 两项 static reference 用例：当前 Linux 环境的 Windows API 缺口。
- `test_client_control_actions` 一项 `test_unknown_recognition_does_not_replace_persistent_toggle_semantics`，以及 `test_client_control_stability` 全部九项：旧 QML 替身/旧期望失败。本次不顺带改旧控件行为。

新增独立 QML 实例测试检查控件四个按钮的实际窗口边界、鼠标选择及两行布局，并在 540 / 1100 宽的主页检查选择器和键盘操作。图库原有离屏布局测试继续覆盖实际页面。这些仅为 offscreen 证据，不是 Windows 原生窗口/游戏验收。编写新布局测试期间出现过测试对象销毁次序导致的崩溃，修复延迟销毁后重新执行最终测试，不能将此前失败计为通过。

设置页另有实际 `ClientSettings.qml` 实例测试：键盘选择作者后运行态不变，撤销恢复草稿，再选择并触发保存按钮信号，通过真实 SettingsService / Coordinator / SessionService / LiveRuntime 验证作者落盘且生效；原生输入/窗口发现及显示消费者为替身。S1 同难度的旧作者列表、迟到激活和 busy 状态不会写入偏好。主壳的 `routeOptions` 生产绑定单独在 Qt 中执行，核对双方均为 S1 时不追加旧自定义图库、恢复 S0 时恢复选项；这不是完整主壳端到端测试。
