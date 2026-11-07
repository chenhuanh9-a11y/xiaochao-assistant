# S0 楼层契约准备

本改动基于 `4b35a97`，只整理已存在的 S0 楼层身份和横向控件次序。不登记 S1 楼层，不改变检测阈值，不声明资源或实机能力。

## 已接通范围

- `app/floor_contract.py` 提供不可变的已复核契约：S0 噩梦 `-1/1/2 → B1/1/2`，S0 困难 `1/2 → 1/2`。按钮次序不等于楼层 ID；转换不猜测、不跨模式借层。
- `floor_control_region.read_floor_in_region` 和 `locate_floor_row` 读取契约，保留原算法。未传 season 的旧调用仍为 S0；显式传入未登记赛季返回不可读。
- `apply_floor_region` 在本帧上下文为非 S0 时丢弃旧固定检测器的正读数，自动、固定、框选三种设置一致。Qt `_observe` 必须以实际生效的 `self._season` 覆盖传入的设置快照，不能把已保存但尚未应用或应用失败的 `gameSeason` 当运行态。不会改写持久化设置。没有活动楼层或点击中心随旧结果流入下游。
- 旧 `MODE_FLOORS`、`FLOOR_NAMES` 导出保留原字典/元组形状；修改这些兼容副本不会注册新上下文。

该契约不负责入口层、源高度、显示文案、资源加载、身份或贴合授权。S1 展示目录仍不能授予检测或资源能力。

## 验证

使用既有 Qt Python 环境，设置 `QT_QPA_PLATFORM=offscreen`、`PYTHONPATH=app:experiments/qt_quick`：

- `python -m unittest discover -s tests -p 'test_floor_contract.py' -v`：8 个测试，覆盖旧层转换、不可变性、未知上下文、三种设置拒绝跨季旧读数。
- `python -m unittest test_client_floor_region test_client_game_floor_click test_client_game_floor_input test_client_floor_ready_policy test_client_known_floor_recovery -v`：51 个现有测试通过。
- `python -m unittest test_client_season_preparation test_season_settings`：13 个赛季准备/设置测试通过。环境有 Fontconfig 缓存警告，不影响该次测试结果。
- 对 `git show 4b35a97:app/floor_control_region.py` 的旧实现逐项比较：两模式所有 5 个楼层，每层 5 个滑动高亮种子；比较框选、自动定位以及三种设置下的已知/未知固定读数，共 200 个完整结果字典一致。使用现有 `test_client_floor_region.hud` 合成输入，不是实机验证。
- `test_floor_observation_evidence.py` 在 Linux 导入旧 Windows 检测模块时因 `ctypes.windll` 不存在而失败；相同基线得到同一错误。未用替身把该项宣称通过，需 Windows 补测。

## 剩余接线

当前只闭合区域读数这一小段。`session_state_machine`、`floor_observation_policy`、Qt LiveRuntime 内部转换、点击命中、校准界面、图库/自定义路线、跨楼层位姿仍有 S0 专用集合。它们没有被声称已经支持新赛季。

独立审查确认一个已有边界：`client_floor_region.py` 的校准预览仍未传 season，旧缓存帧也未绑定 season，因此切季后可能继续显示 S0 框选预览。本次运行时读取 guard 不允许该读数授权楼层，但不能把本提交称为全部 UI 完成赛季隔离。

未来在真实楼层/控件/资源确认后，须显式传递当前赛季和模式，先完成新的检测器布局接缝及读数契约，再接状态与点击。不能仅新增一条契约就复用横向检测器；纵向控件、斜墙、入口数据和跨层坐标桥需要独立验证。S0 回归继续保留，未知层不得落回默认层。

## 运行态与保存态错配修复

初版 guard 直接读取已保存设置，独立复审发现切季失败后可能误禁仍运行中的 S0。本帧设置快照改为使用实际运行赛季。新增两模式 × 三种设置 × 双向错配的 `_observe` 回归：实际 S0 / 保存 S1 保留 S0 楼层；实际 S1 / 保存 S0 返回未知且不保留点击中心。

真实 Qt `context-error` 和 `context-superseded` 生命周期测试，在通过首页切季、保存 S1 并模拟应用失败/过期后直接调用两模式 `_observe`，确认运行 S0 仍输出楼层。仅像素检测/引擎和原生 IO 为合成替身，设置保存、协调器、运行时、Qt 信号和失败流程为真实代码。不代表 S1 实机支持。

复核记录：初版 `_observe` 在内存回装后，新双向错配测试 12 个子场景均失败；修复后通过。扩展组合 194 项中 191 项通过、3 项失败；在未改动候选基线 `4b35a97` 的对应生命周期模块 120 项中复现同名同因 3 项失败（两项 Linux 缺少 `ctypes.windll`，一项已有 `disabled diagnostic clock` 断言）。这些不能列为通过，也未为本次工作修改。独立复审另执行楼层区域与赛季准备 22 项全过。契约测试 8 项仍通过。

修复后核心组合（floor_region、game_floor_click、game_floor_input、floor_ready_policy、known_floor_recovery、season_preparation、season_settings、context_failure）74 项，48.040 秒，全部通过。日志位于工作树外，未将截图或私人资源提交到仓库。
