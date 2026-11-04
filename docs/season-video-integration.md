# 次日赛季与录屏反馈整合验收记录

状态：独立源码候选，已通过校验的增量 bundle 送交独立 Windows 自动验证；未合并、推送、安装、打包或发布。59d86da 的首次验收记录已因后续独立复核发现接缝而重新打开，以本记录更新后的最终证据为准。已发布的 1.0.7 与原功能工作树保持不动。这里的“录屏反馈”指录屏指出的交互/资源问题，不代表新增录屏编码能力。

## 完整目标与已实现范围

1. **赛季先于难度。** 主页、地图资源、悬浮控件、设置共用“赛季下拉 → 噩梦/困难”。目录集中声明编号/名称；名称未确认时只显示编号，不猜名称。扩展 S2 的合成测试覆盖保存、重启、浏览和运行态，但不授予其 S0 资源权限。
2. **旧数据与运行边界。** 无赛季字段的旧设置保持 S0；旧图库、未知设置项与已有自定义控件布局保留。运行态在准备、本局运行及已排队开始期间锁赛季/难度；图库浏览独立，不改运行上下文。异步拒绝/迟到结果不能冒充已切换。
3. **独立设置与控件入口。** 定点地图不透明度、位置编辑、界面不透明度直接置于设置，无需展开高级项；控件定制位于快捷键下。隐藏悬浮控件不妨碍从设置完成/取消位置编辑。新默认控件包含模式及本局动作，不重写旧用户布局。
4. **图库直接管理。** 展示页保留旧作者卡片/独立预览，并增加精确绑定 packageId 的删除入口与末尾 ZIP 导入卡。展示页和管理页共用唯一文件框/确认框；打开时冻结目标赛季/删除计划，取消、失败、重复操作与忙碌状态不跳转页面或漂移目标。
5. **认可旧图包恢复。** 完整匹配认可清单、无收据的旧资源可进入原升级事务，不要求用户先手工找文件。只读检查、确认后重验、独占租约、回滚及成功后临时副本清理保持。未知、残缺、被改动或共享占用文件不放宽。
6. **整合发现的补强。** 新增其他包 requiresFiles 精确依赖保护，避免恢复/升级改写仍被另一包要求为旧内容的文件；依赖不是所有权，未改变依赖的其他作者图仍可升级。检查及写入前共用重验，坏收据失败关闭。图库预览由实际字体/控件高度预留说明空间，修复宽窗口底部覆盖说明被裁切 5px。

7. **完整生命周期与失败恢复。** 保存 S1 后真实冷重启，返回 S0 自动排资源准备；S1 期间发生资源失效也不遗漏。准备错误不自动自旋，显式上下文切换或新资源变化可重试。主页/控件展示实际已应用环境；异步切换拒绝、异常、取消、普通 end-round 热键取代旧请求后，清理 pending，并保留已保存默认项供用户明确重试，迟到结果不能清掉新请求。
8. **收据合同失败关闭。** 普通安装收据缺失、空或不完整 requiresFiles 一律拒绝，导入与删除共用校验。仅精确认可的 legacyAdoption 可省略依赖。相同文件但不同依赖的重导入明确拒绝且不修改数据/收据；不冒充已完成依赖迁移，也不扩展新 receipt-only 事务。

9. **保留可访问选中信息。** 统一难度选择器使用浅色背景与描边，不再将 primary 深蓝填色当选中合同；补回当前难度无障碍名称中的“ · 已选”，保留屏幕阅读器可获取的当前项信息，不改变可见文字、布局或像素。

## 基线与逐项整合

独立候选分支：`integrate/tomorrow-season-video`。基线为 `9d7fa8024f1f5adf27fb664e2fcd1788d4dc883a`。以下均逐项 cherry-pick，没有整文件覆盖，没有合并生产分支：

| 来源 | 原提交 | 候选对应提交 |
| --- | --- | --- |
| 赛季基础隔离 | 657ee2432a09b5d51be012f779ad1b6af4c17455 | 87cc7f7b9c4a2c3adf8eb76ddd31164b2e99dbb8 |
| 赛季验证记录 | 1bd9835 | 96d3487 |
| 两级赛季目录 | faa20c41e3ea78109551d98fe4fef1ab190f303d | 1fe3acd61aa1058b0fb769c30993b7f62ba82bac |
| 设置/控件 | 1aa39f1c377f30a09883e138973232b034cd9336 | 54ec72bafee6a3570fd7751d503799e9764dbdbd |
| 图库入口 | b41036727dd8a174e0b4115019b3a0a41f900ddc | 93de9bab219c09922f7165579a5dc088e2385661 |
| 资源恢复 | 3c417b8011a37b20a371f33e884eccfce866f8b3 | cd981984566ed50205828a0de48a9518b3540a7b |
| 联合测试替身适配 | 本候选新增 | 2e87ce6dd5b06a418e6ee83746e9f6a81db493b2 |
| 其他包依赖保护 | 685cb0442da33c06dfee92d0ac075edcb520219a | 9d03f9a297dcff79f52acfb7ee5c842b559a7e7a |
| 图库说明高度 | 本候选新增 | a50c0f940f9b6b8de64c30add3f08535f5d3f1b8 |
| 真实图库刷新测试 | 1e32d4295eef36aae2a2fd55c0e64e2229bbbf59 | a51ade5319a7e5145e217aa6a754362e8f491915 |
| 应用上下文后资源准备 | 本候选新增 | de6c62c |
| 准备错误有限重试 | 本候选新增 | 62baaba |
| 真实冷重启/失效/取消回归 | 41e6e86、a5fa3d5 | 3b40ea9、da9a7c0 |
| 收据依赖与 no-op 合同 | 4d8299f0b91e77370c969027d80cc3c72ea9b284 | 5e7074c |
| 上下文失败/取消/重试 | ac50b1982e73a4141973167c462ee0206dd81dd1 | e1ef44b |
| snapshot 双失败最小授权状态 | 89a86bd79379e04da1bdc05f6013f188d863d937 | b429bf9 |
| 可访问难度选中信息 | c3f243391cc3e468123f712065129a4c863e5e74 | 12d6b0c |
| 控件可见合同与旧 fixture | bdb28e3 | a7bc4e4 |

无文本冲突。自动合并涉及 ClientSettings.qml、star_map_import.py、client_resources.py；复核保留两级选择器、独立设置布局、`_require_s0`、season 参数、确认指纹及恢复提示双方意图。没有改版本、Setup spec、发布或资源锁文件。

## Windows 复核发现及处置

1.0.7 的正式回执和构建源码锚点已由独立 Windows 线程只读核对：源码 9d7fa802、内部版本 1716、发布时间 2026-11-03 15:34:50 UTC，安装包 179727507 字节，SHA256 423d07c9605c487533be9a5cf5b4432a2ad8cd12486f2d7578730cec69c61f0a。没有据此声称两个网盘文件已下载逐字节验证。

c24fcd1 增量 bundle 的 HEAD/tree、134959 字节、SHA256、前置和导入均已在新 Windows 任务目录验证，旧安装与源码目录未覆盖。该阶段 63 模块 821 项为 794 通过、10 失败、17 跳过，直接 116 项通过。但不能只用同名失败将它们全部归为同原因旧问题：一项 control_stability 候选先失败于 primary=False，基线 primary=True 后才失败于旧 fixture 警告。

独立真实 QML 对照确认 primary=False 是统一选择器的浅色描边合同变化。两种模式、启用/禁用四种状态，30 次 busy on/off 刷新均无实例、选中、禁用或背景漂移。另确实发现用户可感知的无障碍退化：旧名称有“ · 已选”，新按钮名称丢失选中信息，Qt 的 checked/selected 状态也不能补足。

12d6b0c 仅给两个难度按钮增加动态 Accessible.name。真实 QAccessible 查询反例先红（6 个缺失选中名称的子断言），修复后启用/禁用、双向模式切换均通过；四张浮窗 grabWindow 与 c24 逐像素比较均无变化。a7bc4e4 维护测试：旧 fixture 补 persistedValues；旧 primary 断言改核真实选择描边；初次颜色采样等待生产 110ms 动画完成后，仍对 30 次被动刷新逐次严格比较；新增两种模式的启用/禁用、颜色、边框与实例合同。没有仅删除断言或改变产品颜色迁就测试。

最新精确源码/测试候选为 a7bc4e468df67bb75acac46fdf4f74c5b1b30857，tree 8408f6b7b0be57697ee8bc52b14962235182504d；后续仅文档。第二个源码/测试增量 bundle 为 2546 字节、SHA256 d222c261231f1df7fd8b1f6511a411e5e1eb2b0df955235bf4bba98a7811d947，前置 c24fcd1，不含图片或私密日志。最新云端复验为 **57 模块、759 项：737 通过、18 跳过、4 个已在精确基线复现的 Linux/API fixture 失败**。直接 15 模块现 **117 项全过**，另控件动作与稳定性 **11 项全过**；各子集不重复相加。55 个变更 Python 文件 AST 与 diff 检查通过。Windows 最终在同一 a7bc4e4 精确源码/测试上重跑相同 63 模块：**823 项，806 通过、0 失败、17 跳过**；直接功能子集 **117/117 通过**。这些与云端和历史阶段是重叠验证，不累计测试数。

Windows 环境为 Windows 11 22631、Python 3.13.3 AMD64、PySide6 6.11.1、numpy 2.2.5、opencv-python-headless 5.0.0.93。每模块独立进程，任务自有 TEMP/TMP；offscreen 字体从已有系统字体复制到任务目录，仅进程级 QT_QPA_FONTDIR 指向该处，无安装或系统设置修改。17 跳过分别为 13 项 Tk/Tcl 环境缺口、1 项未提供私有 ZIP、3 项创建 symlink 缺少权限；未提升权限，Windows 合成 junction 拒绝测试通过。

Windows 独立 QAccessible 探针确认旧 c24 四种状态缺失已选名称，新 a7 全恢复，非当前名称及 disabled 标志正确；checked/selected 无障碍状态位仍为 false，选择通过动态名称表达。四组实际离屏截图逐像素差与最大通道差均为 0。完整悬浮 UI 的三种状态各 30 轮刷新，实例/颜色/边框/禁用稳定，禁用实际鼠标点击发出 0 次 clicked；没有用它替代真实 Windows 屏幕阅读器、DPI、原生热键或游戏验收。

Windows 最终报告由独立线程 01a1a265-6744-75ca-b1bd-f725b42bb018 产出，报告文件 WINDOWS-VALIDATION-a7bc4e4.txt，SHA256 0cdbad6ba6080f6f5b7546b917433dc3a3c4ad2cc5df2b7149ccc5d7cea6756e。候选、旧 c24 和 1.0.7 基线三个隔离工作树均保持干净。详细最终日志在该 Windows 任务 evidence/a7-full，汇总及校验在 evidence/a7-final；云端新回归位于 work/integration-evidence/regressions-post-windows，视觉/无障碍矩阵在 selector-style。只交付文本源码/测试 bundle 与合成技术报告，没有上传用户图片或私密日志。

## 首轮云端验证（b429bf9，历史记录）

2026-11-03，Python 3.12.14、PySide6 6.11.1、OpenCV 4.12.0，Linux Qt offscreen/software。本阶段产品源码为 `b429bf9615d9e51a796de0fa84e1a245840e61c3`，其后 c24fcd1 仅验收文档与 CI 模块清单。后续 Windows 复核与可访问性修复以上节为准。以临时目录、合成声明/PNG/ZIP、受控 native 替身运行，未连接游戏或读取用户安装数据。

最终逐模块独立进程共 **57 模块、757 项：725 通过、18 跳过、14 失败**。直接覆盖本轮功能与新增接缝的 **116 项全部通过**，属于 757 项子集，不重复相加。14 个失败身份与干净 9d7fa80 基线逐项比较完全一致，没有新增失败。独立复核在同一 b429bf9 再跑 **19 模块、256 项：254 通过、2 跳过、0 失败**，其结果与前述集合重叠，不累计；跳过是未提供私有 ZIP 和非 Windows junction 检查。原始冷重启、上下文异常和 end-round 取消复现均已关闭。54 个变更 Python 文件 AST 与 git diff --check 通过。首次 52 模块/669 项保留为历史，不用于声称覆盖后续修复。

- 其他包依赖保护的最初 7 项回归先红（含子用例共 9 个失败），正式扩展为 9 项后全绿；验证拒绝后不领养、不覆盖，依赖字节不变时可正常更新，以及确认后依赖变化的再检查。
- 真实 Backend、ResourceService、PackageManagerService 与 SessionService 共 3 项服务集成：可选作者导入/删除使真实 profiles、model、预览更新；S1 浏览期间 S0 更新无串季；恢复 S0 保留选择，图片 URL 和解码像素更新。基础作者删除保留缺图声明，但可用数/预览归零。不是 FakeBackend 固定列表，也不是实际 QML 图片渲染证明。
- 真实 QtClient、QML、设置落盘与独立进程重启覆盖保存 S1→重启→返回 S0、S1 中资源失效、快速切换、迟到取消、准备失败不自旋与关闭后不再排队。原生输入/采集和重型识别构建使用替身，资源准备调度及 Session/Coordinator 所有者链真实运行。
- 收据合同新增 12 项反例/正例：缺失、空、畸形、缺项依赖失败关闭；精确 legacy 兼容；不同依赖 no-op 拒绝且原收据/字节不变；同合同 no-op 保留 inode/历史 witness；确认后合同变化拒绝。独立作者还复核删除路径不会绕过依赖保护。
- 完整 QtClient/QML 的失败选择与重试用实际鼠标执行，验证界面保持 applied、默认项仍保存、pending 清除、重新选择默认项可重试。额外覆盖普通 end-round 热键取消、明确拒绝、相同目标幂等、不同目标取代、refreshRuntime 和迟到终态只通知一次。不能用组件固定 root 或仅直接发信号替代这些生命周期证据。
- 最后硬化在回读 snapshot 本身也失败时，仅保留实际上下文与本局锁，丢弃旧的嵌套授权/身份字段；普通命令取代上下文请求后重新核对新权限，不使用回读前的缓存命令授权。反例证明的是 snapshot 字段残留与缓存命令接缝，未证明或声称真实显示像素泄漏。
- 2e87ce6 只修复两处已过期测试假设：coordinator applied context 由二元变三元；模式设置现在一次保存赛季与难度。收据相关旧成功 fixture 补齐真实基础依赖闭包或改成精确认可 legacy，不放宽产品行为。
- 初次探索误用了仓库不存在的 test_star_map_import / test_star_map_download 模块，后续独立审查也曾误用工作任务名当模块名；这些 loader errors 均不计真实测试数。已改用仓库现有模块。
- 少数旧 Qt fixture 不支持任意同进程顺序组合，独立审查曾遇到退出 139。最终按每个模块各自进程运行；不把探索性崩溃算为通过，也不称任意组合生命周期已解决。

可复跑命令（每个模块单独进程）：

```sh
export PYTHONPATH=app:tests:tools:experiments/qt_quick
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software
python -B -m unittest -v <module>
```

本轮直接模块：`test_season_settings`、`test_season_catalog`、`test_season_selector`、`test_season_gallery`、`test_season_resources`、`test_season_session`、`test_settings_accessibility`、`test_client_gallery_actions`、`test_client_gallery_layout`、`test_client_gallery_resource_refresh`、`test_legacy_import_recovery`、`test_package_dependency_upgrade`、`test_receipt_dependency_contract`、`test_client_season_preparation`、`test_client_context_failure`。现有 Windows 联合工作流增加 season-ui 分组及依赖保护测试；本阶段未触发远端 CI；Windows 自动验证结果见上节，不能把本阶段云端结果冒充 Windows 结果。既有工作流中的历史比较 commit 未改动，未来执行前须另核对当时的已发布比较锚点；本轮云端基线比较明确使用 9d7fa80。

### 14 项基线失败

在独立、干净的同基线 9d7fa80 同环境逐模块复跑，失败身份一致：

- `test_client_live_runtime` 2 项 static reference：当前 Linux 的 Windows API 替身/原生能力边界。
- `test_client_control_actions` 1 项、`test_client_control_stability` 9 项：旧控件 fixture/期望问题。
- `test_client_live_controls` 1 项 HWND 复用：fixture 未注入 native binding probe 的 lost 结果，不能凭 catalog 变化替代该合同。
- `test_map_resource_context` 1 项：Python 3.12 Path.resolve() 内部 stat 与测试的 no-stat 替身断言冲突。

这不是全库全绿，也不把基线失败当成不需后续处理的产品保证。

### 离屏截图

补拍 **12 张完整 QtClient 图**：正常/失败/重试成功主页各 650/1200 宽，三种悬浮状态、真实鼠标下拉重试，以及 pending/failed 转换全窗图。生成于 e1ef44be；最终 b429bf9 全部生产 QML SHA256 与截图时相同，且在最终源码单独重跑 context-error 成功并完整关闭。650 为真实主窗最小宽度；旧 540 宽只能称独立组件，不是生产主窗尺寸。

失败后主页仍显示实际 S0，保存默认 S1 的差异/重试说明在两种宽度完整可见；悬浮下拉实际选 S1 可成功重试，S1 资源未登记，开始仍禁用。悬浮失败态本身没有独立差异说明，外观仍如正常 S0；完整说明在主页，这是已记录的体验限制。

原 16 张图明确保留为历史：旧主页/控件已被新图替代。图库/设置仅复用未变 ClientGallery.qml / ClientSettings.qml 哈希对应的布局证据，不能将旧共享选择器图说成最终 retry/disabled 合同验证。图库宽页说明 bottom/viewport 为 347/347 或 597/597，无遮挡。旧 540×740 设置三项显示入口需要滚动，无需展开高级项，不能称首屏可见。

某张旧 S1 图库交互图使用固定目录 fixture，只证明当时选择器外观，不能代表真实 S1 资源；隔离以真实 Backend 测试为据。最终捕获时 QML warnings 为空；完整退出后对象销毁阶段的 null-service 警告另行记录，不能称整个进程无警告。28 个 PNG 的来源、适用边界及 hash 均在 screens/README.md、screens.sha256 与 product-files.sha256 中逐项明确。

原始命令、日志、截图、几何与 SHA256 记录保存在本候选的忽略目录 `work/integration-evidence/`，不提交用户日志/素材。最终回归为 `regressions-reviewed/summary.json`，历史首次回归为 `regressions-final/summary.json`，基线对比为 `baseline/summary.json`，独立最终审查为 `independent-review-b429bf/summary.json`，失败/取消回归日志为 `context-failure/`，截图为 `screens/`。

## 未完成与下一步验收条件

### 2026-11-04：跟进主线 #94 与图库空卡

本轮先保留并提交原工作区仅有的两个已审查图库修改，得到 `24939c2`；随后将主线 #94 的实质修复 `8a416f798d82f0b5b6237accf43934830ee762fe` 逐项 cherry-pick 为 `49f56ca911bcc96da125b86bb194edf6c4ea788c`，无文本冲突。最终源码 tree 为 `e4bc1989357f16448f014bcaefa19ca63ff85b92`。未合并分支、推送、修改版本、安装或发布。

实际 fetch 核实当时最新 `main` 为 `164ea363c8d38c0d6d12353379add595fc26ee07`，其相对原基线 `9d7fa80` 仅有 #94 的三个文件变更。候选与 main 仍从该基线分叉，不能称候选是最新 main 的后代；`git cherry HEAD origin/main` 确认 #94 实质提交已具有等价补丁。诊断链中的 `invalidationOrigin` 经 runtime 事件、coordinator trace、磁盘和诊断导出保留，不改变赛季隔离。

图库仅在 Qt 展示层过滤 `available_layers == 0` 的作者，底层声明和包修复能力继续保留。新增真实合成 PNG/JSON + 包事务测试覆盖：初始空卡隐藏，删除后隐藏及清理预览，重新导入恢复，删除所选作者自动回退，重复刷新保留有效选择，S1 期间删除后返回 S0 的回退。部分图片仍可用的作者继续显示；没有将“本地文件存在”扩大为内容校验或安装收据有效。

在 `49f56ca` 精确源码上，以 Python 3.12 / PySide6 6.11.1、Qt offscreen/software、各模块独立进程执行本记录上述 15 个直接模块及 `test_client_problems`、`test_client_coordinator`、`test_client_gallery_count`、`test_client_control_actions`、`test_client_control_stability`，共 **20 模块、188 项全部通过，无跳过**。每模块命令为 `python -B -m unittest -v <module>`，`PYTHONPATH=app:tests:tools:experiments/qt_quick`；日志和 JSON 汇总在忽略目录 `work/integration-evidence/regressions-main94-empty-cards/`。新增的五个变更 Python 文件 AST 与 diff 检查通过。这是本轮针对性回归，不复用旧 Windows 823 项作为新源码通过证据，也不宣称全库通过。

只读运行 `tools/build_qt_client.py --check-environment` 明确返回 `success: false`：当前不是 Windows x64 Python 3.13，缺 Windows Qt 平台插件、PyInstaller/相应 hooks/pefile 和显式 Inno Setup 编译器。未尝试在此环境制作 Windows 安装器。下一步测试包仍须将精确候选送到独立 Windows 工作目录，针对新增代码重跑回归，使用已授权且有 hash 库存的 S0 资源和干净源码进行完整 freeze，选择独立 `client` 测试实例（不能用 production 覆盖旧安装），校验最终 Setup 的 manifest、源码/资源/版本回执、字节数与 SHA256。源码改变，不能只重编旧 Setup 外壳。按仓库交付合同，正式可交付构建还需绑定同一远端 commit；本轮禁止推送，因此该步骤仍待后续授权。已有 CI 的历史 baseline 锚点不代表本轮 main，执行前需按预期比较目标重新核对。

S1 真实资源和名称仍缺失，但不阻挡验证 S0 兼容、S1 失败关闭、选择器/设置/图库交互与诊断链。测试包只能如实展示 S1 未就绪，不能以合成测试替代 S1 资源接入或真实游戏验收。

- **S1 实际资源仍缺失。** 需要有来源的结构地图、索引/楼层、门/坐标合同、逐层显示绑定、复核相似组及资源版本/hash。当前清晰显示不可用，不借用 S0；不能仅修改目录就宣称资源可用。
- **名称仍需权威确认。** 当前只显示 S0/S1，不编造赛季中文名。
- **Windows 原生与真实游戏未验收。** 上述 Windows 自动验证使用合成输入/采集与重型引擎替身，不能替代独立候选安装环境中的 DPI/字体、无焦点 popup、输入穿透/全局快捷键、准备及运行中锁、隐藏控件独立设置、位置编辑完成/取消、旧配置与真实图库保留、文件选择器、退出后 worker 排空。
- **依赖迁移边界。** 已领养旧包与同字节完整 ZIP 之间的纯依赖变化也明确拒绝，不自动迁移收据。真正文件内容改变的合法旧图升级仍按原事务支持；未知损坏收据不以兼容名义放过。
- **资源实物边界。** 录屏中的具体未知冲突 ZIP 未作为复现输入，本轮只修复已证明的认可旧包与依赖/上下文合同问题；真实包与未知冲突不能宣称全部已修。下载网络与 Windows 文件句柄事务也不由本轮合成测试替代。
- **候选未发布。** 没有远端 CI、Setup 构建、安装、合并或发布。后续若获准发布，须对最终同一远端 commit 做联合检查、干净构建与安装验收，另行核对发布范围，不改写 1.0.7 公告。
