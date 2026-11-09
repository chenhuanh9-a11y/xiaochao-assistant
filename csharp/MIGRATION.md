# 星图完整 C# / WPF 迁移

当前是独立实验分支 `codex/full-csharp-wpf-20261109`，来自 PR #125 的 `29dd3f24cdaf46f79b9ce401ab5b0884b45194af`。Python/Qt 源码基线为 `7991dbbecdb66d76799415fb555d2c8a0361acc6`；PR #124 的计划提交为 `dc464ff2cf37deaa7cacc60c04639615e59d2def`。不替换正式版，不合并 main，不发布正式版本。

目标是包含原功能与完整 UI 的纯 C# 应用，依赖 Python 的中间状态不能作为完成。此前阶段一网格壳和旧 hybrid 测量均不是全量版本。

**当前真实画面要求：只使用 2026-11-05 及之后采集的原始画面。** 用户指出此前楼层控件位置已经改变。下面提到的 10 月原帧对照仅保留为历史数值移植证据，不代表当前楼层观察、识别或使用流程可用，也不再重跑旧图。新画面须核实原始采集记录和当前控件位置，不能把复制后的文件时间当采集时间。原结构资源兼容、纯状态与明确标注的合成数值测试单独记录。

## 实现顺序与验收矩阵

| 范围 | Python 真实入口/负责人 | 本轮 C# 状态与完成证据 |
|---|---|---|
| 启动/主窗口与持久页面 | tools/qt_client_entry.py → qt_quick/client.py / Client.qml | 实际 WPF 构造/持久页面回归通过；真实界面验收待完成，未接入页面有明确状态 |
| 资源图库、图包、按层结构 | client_providers.py / shared_structure_reference.py / client_resources.py | 只读 catalog/NPZ 已实施；15 个跨模式/楼层代表与 NumPy 回读一致，15 个类型/坐标/wall 反例及 20 个非 object JSON 反例通过；轮廓回退、解码预算、坏图/缺结构/文件占用恢复有实际入口回归；图包安装/版本锁与完整 provider 待接入 |
| 设置与自定义路线 | client_settings.py / client_custom_gallery.py / client_paths.py | 实验副本/批量设置/原件保护兼容回归 73 项通过，包含损坏尾项显式解绑；实际 WPF 保存期间编辑及导入编辑禁用/恢复通过；图库编辑与运行消费待完成 |
| 捕获、窗口绑定与输入 | client_capture.py / client_native_keys.py / client_input.py | 11/09 生产 GDI 完整客户区实采 1334×750；WGC 三轮软件呈现自有窗口及取消/关闭 12 项通过，默认硬件呈现同窗口仍白/黑帧失败，未证明游戏 WGC；pass-through 键盘/Raw Input 模块 406 项及原生启动/停止通过，WPF 尚未启用全局监听消费 |
| 楼层/开图准备 | client_live_runtime.py / map_open_preparation.py / floor_contract.py | 今日真实帧加新合成共 37 案与原 Python 严格逐字段一致；含区域设置畸形值修前反例、修后回退。真帧读为地图打开/1F；困难/S1 仅合成。准备沿原 1.05s/50ms/最多 21 probe；Runtime 30 项包含截止时间、未锁延迟首探与锁图立即首探，不是实机准备延迟验收 |
| 身份与入口竞争 | map_identity_engine.py / difficult_entry_identity.py / difficult_side_entry_identity.py / front_door_recognition.py | passport 清单/类型与 MainEntry 全域生产搜索已迁；今日原帧及新合成 7 案严格 0 差异，墙面栅格也 0 差异。今日初始房间只有 9 个入口角点，3 个候选均未确认身份，未冒称自动认图成功。侧门竞争、S1 structure scan、相似组等完整消费者仍待移植 |
| prepare/grid/refine/propose/验证 | fast_structure_alignment.py / single_map_structure.py / nightmare_candidate.py | 原帧观测/候选/精修与拒绝 7 案严格一致；单楼层 prepare 的 2,343,600 距离值、120 链/ray 一致；非整数 grid 4 实际输入修后精确一致。新增原 Python production provider/fast attempt/验证消费者独立重算，6 个适用 fit 案的矩阵、点、区域、墙及数值残差逐字段一致，3 个通过；不包括 canonical 信任字段、fallback 或完整身份链 |
| 会话/取消/缓存/恢复 | client_session.py / client_coordinator.py / client_live_runtime.py | SessionService 单写者/单 worker/取消/资源 lease/真实回调重入与 Python 纯状态 43 项通过，真实 Runtime 30 项通过；含同请求进展原子读取及无下一次刷新时的最终撤销校验。WPF 到真实 Session/Runtime 的当前帧回放与设置/资源/关闭有 46 项通过记录，但最终轮次有一次首次读图失败尚未解释，见下方限制。原 steady/fallback、恢复调度与 canonical/display 尚缺，LiveAuthorization 保持 false |
| 结构/作者/浮窗/穿透/DPI | client_windows.py / ClientAligned.qml / ClientReference.qml / author_display_policy.py / nightmare_author_coordinate_adapter.py | 待接入；作者只参与显示，不授权身份或位姿 |
| 账号/路线广场/投稿 | account_service.py / client_route_plaza.py / ClientAccount.qml | 待迁；不以演示数据或按钮提示冒称接入 |
| 教程/公告/帮助/诊断/更新 | client_info.py / update_service.py / ClientHelp.qml 等 | 待迁；关闭/后台恢复、状态与错误路径需覆盖 |
| 自包含 Setup/数据迁移/升级回退 | 当前 Python 安装更新流程 | 待交付完整实验 Setup；干净 Windows/中断/回退仍缺可恢复环境 |

状态随实现与证据更新，不因页面存在就改为功能完成。每次交付记录源提交、资源来源、真实输入、构建、GUI 与未测项。Python 原件、玩家数据和历史失败证据保持可恢复。

## 技术与视觉选择

新 WPF 产品采用 .NET 10 LTS（SDK 10.0.403）与固定 WPF-UI 4.3.0，沿用现版浅色/蓝色与持久导航、游戏独立浮窗。Core 不依赖 WPF 或 Windows App SDK。初期自包含、不裁剪；具体体积以完整 Setup 测量为准，不承诺约 1/3。

已比较 MaterialDesignThemes 5.4.0（2026-10-20）和 WPF-UI 4.3.0（2026-05-04），均 MIT、NuGet 提供 net10.0-windows7.0。选 WPF UI 因 Windows 原生导航、焦点/控件与现有多窗口操作更适合本产品；不将 Material 动效/大面积控件样式强加给游戏工具。原版权/许可随包携带。可访问性与性能仍要在真实 UI 核对，维护活跃不等于已验收。

来源：[.NET 生命周期](https://dotnet.microsoft.com/en-us/platform/support/policy)、[WPF UI releases](https://github.com/lepoco/wpfui/releases/tag/4.3.0)、[WPF UI NuGet](https://www.nuget.org/packages/WPF-UI/4.3.0)、[Material Design releases](https://github.com/MaterialDesignInXAML/MaterialDesignInXamlToolkit/releases/tag/v5.4.0)。.NET 8 支持截止 2026-11-10，新完整迁移选择仍受长期支持的 .NET 10。

## 数据和同步

完整客户端入口是 `tools/qt_client_entry.py`，不是 `qt_quick/main.py` 图库实验。原结构资源优先兼容现有 index/schema/NPZ 与每层坐标；实验旧资源来源不等于当前 main 锁已验证。绝不改资源、抬阈值或伪造缺项来凑通过。

本机与云端以明确 Git 提交/分支交接，按模块单写者集成；不强推、重置、覆盖未交接修改。首个实验保存点 `7b44066859a66bb38660a6113aa82d567fb8c6bb` 已推送并回读远端分支 SHA；本轮新增模块仍待最终复核后提交。云端工作区未直接查证，不能称源码工作区完全同步。

旧 hybrid 预定小时测试因新全量目标正常停止：实际 679.983121 秒、136 请求、1 次暖机接受差异，完成标记 false；无已观测自有残留。保留原始数据与差异，不称一小时通过，也不将它当作新纯 C# 版本测试。

## 当前证据与限制

- 资源目录声明 254 项；262 条缺图问题分为 130 个楼层 image 与 132 个 preview 缺失。`Ready` 只是绑定声明/路径状态，不代表 254 个数组、所有坐标桥或完整资源链均已验收。compact shared_mask 的 clean/validation 数值相同来自显式角色声明，不是两份独立几何证据。
- 设置测试不等于完整设置消费者已迁移。尚无跨进程 compare-and-swap 保存保证，祖先 junction 路径防护与 S0 路线偏好投影仍需接入；当前只写显式实验目录。
- 当前窗口身份复核包含 HWND、进程启动、PID、线程及类，能拒绝跨进程陈旧候选；尚未监听窗口销毁/重建，同进程同线程同类复用 HWND 的生命周期缺口仍开。共享捕获 port 只在采集时要求目标前台；之后按绑定/几何/帧代际校验，允许返回助手确认候选。单次预览不替换运行帧。WGC 缺能力才选择 GDI；设备失败或上下文变化不自动绕到 GDI。原 overlay-exclusion fallback 仍缺。
- 首个界面预览因图标引用相对路径错误退出，已修正并通过真实 WPF 构造回归；构造/导航测试没有调用 Show，不能代替可见界面检查。WGC 诊断中默认 WPF 自有窗口原生画面白/黑而离屏逻辑图正常；同一程序显式软件呈现后真实 WGC 全通过。没有修改应用的全局渲染偏好，不把默认失败改称成功，也未证明游戏后端可用。
- 用户报告的结构黑块来自原二值区域被整片深色填充，已改为浅色区域与实际四邻域轮廓；不修改原数组或将缺失的完整楼层图伪称已补齐。WPF 回归 21 项含实际 catalog → 选择 → 解码失败 → 有效图恢复和设置真实 I/O，没有 Show。用户另在新版独立测试窗口确认“已显示浅灰区域和轮廓”，仅限该预览的可见结果。
- 新增 `StarMap.Vision` 不启动 Python，但几何证据不自动授权地图身份、canonical 位姿、会话提交或上屏。上述完整消费者仍需实现与真实流程验证。
- Vision 固定 OpenCvSharp4 与 Windows slim runtime 4.13.0.20261107，主许可证随项目输出；完整原生组件 build-specific notices 仍需在包含 Vision 的 Setup 分发前补齐。WPF 已通过 Runtime 引用 Vision；当前仍未打包为完整实验 Setup。
- 最终 WPF 冷进程回放 `workspace-runtime-final` 在首次真实 Floor/Main 消费断言处失败，当次未保存该中间状态，原因未确定。随后只增加中间状态记录，不修改生产算法、时限或断言，`workspace-runtime-final-02/03/04` 均为 46/46；这些通过不能抹去第一次失败或证明稳定性。后续需用保留的中间状态定位复现；不为凑通过放宽默认预算。

本轮可回到的证据：`full-csharp-validation/live-nightmare-client-20261109-03` 是当前原始客户区与采集事件；`floor-python-current-04` / `floor-current-after-raw-01` 是 37 案原入口对照；`main-entry-current-python-03` / `main-entry-current-replay-03` 是当前 MainEntry 对照；`session-after-final-ownership-tests.txt` / `runtime-after-final-ownership` 是 43/30 项最终生产接缝；WPF 各目录按上文分别记录失败和通过。WGC 的 `wgc-owned-fixture-03-software` 与 `03-default` 分别保留成功和失败，均不是游戏验收。旧失败目录保留，不以重试覆盖。
