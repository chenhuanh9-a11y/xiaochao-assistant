# 赛季与难度上下文

## 身份和兼容边界

`app/season_context.py` 是共享的赛季身份契约。`season` 取集中目录中的 ID（当前为 `s0` / `s1`，可增加 `s2` 等），`mode` 仍取 `nightmare` / `difficult`，不把赛季塞进旧难度字段。完整键为 `season:mode`，当前目录产生 `s0:nightmare` 等四种组合，新增目录项自动扩展。识别、贴合和对局生命周期仍共用原算法。

没有 `gameSeason` 的旧设置读取为 S0，读取本身不重写文件。`contextRouteProfiles` 保存完整上下文的作者偏好。S0 可读取原 `modeRouteProfiles` / `routeProfileId`；S1 不可借用这些旧偏好。原自定义图库文件及未知设置字段保留。S0 学习目录与设置文件位置不变；S1 学习目录预留为 `work/seasons/s1/<mode>-memory`，不读取 S0 样本。

## 所有者与失效

- coordinator 接收运行上下文请求，settings 持久化后由 session 原子提交至 runtime。session 的 desired 与 applied 上下文分开；只有实际 runtime 相符才确认完成。
- 运行中的赛季/难度在 coordinator、session 和 runtime 均受对局锁保护。相同上下文重复选择幂等；相同难度跨赛季是完整切换，不是作者重投影。
- 图库有独立浏览上下文。浏览赛季不会改变运行赛季，也不受对局切换锁限制。主页预览只取 applied 运行上下文。
- worker 请求、回执、列表/预览键、手选上下文和资源缓存包含赛季。旧请求不能覆盖更新后的上下文；资源失效仍通过既有 generation / revision 机制撤销迟到结果。

## 当前资源能力

当前四种组合都有明确身份和能力报告，新增目录中的赛季同样生成独立报告。S0 延用现有资源合同，并继续运行原结构/坐标/哈希验证；“已登记”不表示当前安装一定完整可用。

S1 两种难度均未登记真实地图资源，当前明确不可用。界面可选择以查看缺口，但不能开始识别，不加载 S0 结构、作者、门、地图坐标、学习样本或自定义路线。不以合成 fixture 充当生产地图。

旧 ZIP 只属于 S0。显式 S1 导入目标、S1 manifest 或未知包仍拒绝，不放宽 packageId、路径、依赖和 SHA256 白名单。

正式接入 S1 前需要有来源的结构地图、地图/楼层索引、门与坐标合同、逐层显示绑定、经过复核的相似组（如适用）及资源包哈希/版本声明；然后为独立根目录增加经过验证的资源登记。当前不能仅把 S1 标志改成 available 或指向 S0 目录。

## 验证边界

本次仅云端源码、合成 fixture 和 Qt offscreen 回归。Windows 安装、真实游戏捕获/快捷键、真实 S1 地图和可见像素验收尚未进行。未修改版本、未打包、未发布。

### 初版四组合选择器的云端验证记录（2026-11-03，657ee243）

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

初版阶段（657ee243，已被后续两级选择器替代）的独立 QML 实例测试检查控件四个按钮的实际窗口边界、鼠标选择及两行布局，并在 540 / 1100 宽的主页检查选择器和键盘操作。图库原有离屏布局测试继续覆盖实际页面。这些仅为 offscreen 证据，不是 Windows 原生窗口/游戏验收。编写新布局测试期间出现过测试对象销毁次序导致的崩溃，修复延迟销毁后重新执行最终测试，不能将此前失败计为通过。

初版阶段的设置页另有实际 `ClientSettings.qml` 实例测试：键盘选择作者后运行态不变，撤销恢复草稿，再选择并触发保存按钮信号，通过真实 SettingsService / Coordinator / SessionService / LiveRuntime 验证作者落盘且生效；原生输入/窗口发现及显示消费者为替身。S1 同难度的旧作者列表、迟到激活和 busy 状态不会写入偏好。主壳的 `routeOptions` 生产绑定单独在 Qt 中执行，核对双方均为 S1 时不追加旧自定义图库、恢复 S0 时恢复选项；这不是完整主壳端到端测试。

## 赛季目录与两级选择器（2026-11-03 后续 UI 调整）

主页、地图资源页、悬浮控件和设置页共用 `SeasonSelector.qml`：先用下拉框选择赛季，再选择噩梦/困难。不是将四种组合写死在各页。重复选择当前值不会发请求；异步申请未确认或被拒绝时，下拉仍显示所有者的实际赛季。设置页仍编辑草稿，保存/撤销语义不变，图库浏览仍独立。

`app/season_catalog.py` 是统一纯数据目录，每项声明 `id`、`number`、`name` 和两种难度的资源登记状态。当前 S0/S1 名称尚无确认来源，留空并只显示编号，不采用语音猜测的名称。未来新增 S2 等只需增加目录数据项；服务允许值、上下文键、设置校验、学习目录和 UI 模型随目录生成。

目录 ID 必须为唯一且与编号一致的 `s<number>`，名称仅用于显示，不用于路径。空目录、重复/畸形 ID、编号不匹配、非法名称或试图把非 S0 标为旧资源均明确报错。目录只描述资源状态，不能授予新赛季 S0 资源访问能力。所有非 S0 运行/浏览/自定义显示入口继续失败关闭，错误显示对应目标赛季，不把 S2 报成 S1。

目录采用静态导入的 Python 数据模块，沿用 `tools/qt-client.spec` 的 `a.pure` 和 app 源文件收集；共享 QML 由既有 `*.qml` 收集覆盖，不依赖未打包的新 JSON 或源码相对读取。验证包含模拟 frozen 标志的导入烟测与现有 spec 路径核对；未实际打包，不能称成品闭包或安装验收通过。

新增独立进程测试仅替换临时目录模块、加入合成 S2 条目，再启动实际 settings/backend/coordinator/session/runtime/provider 消费链，确认 S2 可保存、重启、选择、产生独立能力和样本路径，但不构造/捕获/借用任何 S0 资源。UI 的 S2 名称是明确标注的测试输入，不是产品名称。

后续设置页测试把保存/撤销也升级为可见边界内的真实鼠标点击；使用真实 QML、设置持久化、协调器、会话和运行态，仍以替身代替原生输入/窗口发现及显示消费者。共享选择器另覆盖真实下拉鼠标选择、难度选择、重复操作、禁用、延迟确认和窄宽布局。

后续两级选择器验证：Qt 21 模块仍为 335 项（331 通过、1 跳过、3 项原有失败）；六个赛季模块合并为 51 项全通过，其中 `test_season_settings` 的 7 项已经包含在 Qt 组内，不重复累计。其余五个模块为 44 项，包含目录 3 项和共享选择器 3 项的新用例。

控件集成测试直接核对实际 `ClientControls.qml` 窗口的 `WindowDoesNotAcceptFocus` 标志，用鼠标打开赛季 popup 并选择 S1，检查请求、关闭及未确认时保持旧实际季；置对局锁后鼠标不能再打开或请求切季。仍是 offscreen 而非 Windows 原生验证。

本轮额外重跑浏览资源和旧控件稳定性共 20 项，10 通过、10 项同基线失败；加上 Qt 组的 3 项，仍为同一组 13 项既有失败，没有将重复运行累计。前述初版 38 项赛季组已被本轮五组 44 项取代，不能与其相加。
