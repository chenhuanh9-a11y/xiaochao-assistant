# 阶段一模块对照

C# 侧只实现表中“阶段一”列。其余是计划中的对应位置，不是已经移植。噩梦与困难以后仍走同一套识别核心，用资源提供器和楼层参数区分；本阶段的合成帧不覆盖任一模式的真实调用。

会话与请求序号只有一个写者：`StarMap.Core.Session.SessionSequencer`。它独占会话号、代际、`LastIssuedSequence` 和 `SupersededThrough`。WPF 窗口、`SliceRunner`、网格函数和 `ResultGate` 都不能改这些字段。`ResultGate` 只记录某一次结果是接受、过期、取消还是重复。识别真值仍不属于界面；现版提交位姿的是 V2 所有者（`app/alignment_pose_transaction.py` 一带），阶段一还没有移植提交者，因此 C# 结果停在候选，不写显示。

| 现版 Python/Qt | 计划中的 C# | 阶段一 |
| --- | --- | --- |
| `experiments/qt_quick/client_session.py` `SessionService` | `SessionSequencer` | 已实现，唯一序号写者 |
| `app/session_state_machine.py` | 只读谓词，不另设写者 | 未移植 |
| `tools/qt_client_entry.py`、`experiments/qt_quick/client_windows.py` | `csharp/src/StarMap.App` WPF 壳 | 窗口壳已写，仅 Windows 构建 |
| `experiments/qt_quick/client_native_keys.py`、`client_input.py` | `HotkeyPolicy` | 政策与不消费桩；不安装钩子 |
| `app/fast_structure_alignment.py` `sample_edges`、`_grid` | `GridSlice` | 已 1:1 移植 |
| 同文件 `prepare`、`propose`、`refine`、`try_alignment` | `StarMap.Core.Recognition` 后续类型 | 未移植。不改 40 点、96/300 抽样、3/6 像素、0.50 与 0.06 |
| `app/nightmare_candidate.py`、`app/map_identity_engine.py`、`app/difficult_native_identity.py` | 同一识别核心的身份阶段 | 未移植。护照、已知通道仍是缺口 |
| `app/difficult_grid.py`、`app/difficult_mode.py` | 同一核心，不复制第二套网格 | 未移植 |
| `app/front_door_recognition.py`、`app/front_door_layout_recognition.py`、`app/side_door_recognition.py` | 正门/侧门策略 | 未移植 |
| `app/floor_contract.py`、`app/floor_control_region.py`、`app/floor_observation_policy.py` | 楼层元数据 | 未移植。自动读层未做 |
| `app/alignment_pose_transaction.py` | 位姿提交者（不是界面，也不是序号写者） | 未移植 |
| `experiments/qt_quick/client_resources.py`、`app/star_map_import.py`、`app/star_map_download.py` | 资源事务 | 未移植 |
| `app/optional_author_displays.py`、`app/base_map_display.py` | 显示桥，绑定实际来源 | 未移植 |
| `app/diagnostic_privacy.py` 及 `diagnostic_*.py` | 诊断 | 未移植。不采集窗口标题、用户名或其他程序 |
| QtIFW（`tools/rebuild_qt_installer.py` 一带） | 以后的自包含 Setup | 未构建安装器 |

同轮实验还指出恢复消费者、其他模式/楼层和混合 DPI 没有验收。阶段一不补假资源来填这些缺口。
