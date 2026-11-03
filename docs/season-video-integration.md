# 次日赛季与录屏反馈整合验收记录

状态：仅云端源码候选，未合并、推送、打包或发布。59d86da 的首次验收记录已因后续独立复核发现接缝而重新打开，以本记录更新后的最终证据为准。已发布的 1.0.7 与原功能工作树保持不动。这里的“录屏反馈”指录屏指出的交互/资源问题，不代表新增录屏编码能力。

## 完整目标与已实现范围

1. **赛季先于难度。** 主页、地图资源、悬浮控件、设置共用“赛季下拉 → 噩梦/困难”。目录集中声明编号/名称；名称未确认时只显示编号，不猜名称。扩展 S2 的合成测试覆盖保存、重启、浏览和运行态，但不授予其 S0 资源权限。
2. **旧数据与运行边界。** 无赛季字段的旧设置保持 S0；旧图库、未知设置项与已有自定义控件布局保留。运行态在准备、本局运行及已排队开始期间锁赛季/难度；图库浏览独立，不改运行上下文。异步拒绝/迟到结果不能冒充已切换。
3. **独立设置与控件入口。** 定点地图不透明度、位置编辑、界面不透明度直接置于设置，无需展开高级项；控件定制位于快捷键下。隐藏悬浮控件不妨碍从设置完成/取消位置编辑。新默认控件包含模式及本局动作，不重写旧用户布局。
4. **图库直接管理。** 展示页保留旧作者卡片/独立预览，并增加精确绑定 packageId 的删除入口与末尾 ZIP 导入卡。展示页和管理页共用唯一文件框/确认框；打开时冻结目标赛季/删除计划，取消、失败、重复操作与忙碌状态不跳转页面或漂移目标。
5. **认可旧图包恢复。** 完整匹配认可清单、无收据的旧资源可进入原升级事务，不要求用户先手工找文件。只读检查、确认后重验、独占租约、回滚及成功后临时副本清理保持。未知、残缺、被改动或共享占用文件不放宽。
6. **整合发现的补强。** 新增其他包 requiresFiles 精确依赖保护，避免恢复/升级改写仍被另一包要求为旧内容的文件；依赖不是所有权，未改变依赖的其他作者图仍可升级。检查及写入前共用重验，坏收据失败关闭。图库预览由实际字体/控件高度预留说明空间，修复宽窗口底部覆盖说明被裁切 5px。

7. **完整生命周期与失败恢复。** 保存 S1 后真实冷重启，返回 S0 自动排资源准备；S1 期间发生资源失效也不遗漏。准备错误不自动自旋，显式上下文切换或新资源变化可重试。主页/控件展示实际已应用环境；异步切换拒绝、异常、取消、普通 end-round 热键取代旧请求后，清理 pending，并保留已保存默认项供用户明确重试，迟到结果不能清掉新请求。
8. **收据合同失败关闭。** 普通安装收据缺失、空或不完整 requiresFiles 一律拒绝，导入与删除共用校验。仅精确认可的 legacyAdoption 可省略依赖。相同文件但不同依赖的重导入明确拒绝且不修改数据/收据；不冒充已完成依赖迁移，也不扩展新 receipt-only 事务。

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

无文本冲突。自动合并涉及 ClientSettings.qml、star_map_import.py、client_resources.py；复核保留两级选择器、独立设置布局、`_require_s0`、season 参数、确认指纹及恢复提示双方意图。没有改版本、Setup spec、发布或资源锁文件。

## 最终云端验证

2026-11-03，Python 3.12.14、PySide6 6.11.1、OpenCV 4.12.0，Linux Qt offscreen/software。最终产品源码为 `b429bf9615d9e51a796de0fa84e1a245840e61c3`，后续仅验收文档与 CI 模块清单。以临时目录、合成声明/PNG/ZIP、受控 native 替身运行，未连接游戏或读取用户安装数据。

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

本轮直接模块：`test_season_settings`、`test_season_catalog`、`test_season_selector`、`test_season_gallery`、`test_season_resources`、`test_season_session`、`test_settings_accessibility`、`test_client_gallery_actions`、`test_client_gallery_layout`、`test_client_gallery_resource_refresh`、`test_legacy_import_recovery`、`test_package_dependency_upgrade`、`test_receipt_dependency_contract`、`test_client_season_preparation`、`test_client_context_failure`。现有 Windows 联合工作流增加 season-ui 分组及依赖保护测试；未触发远端 CI，不称 Windows 已通过。既有工作流中的历史比较 commit 未改动，未来执行前须另核对当时的已发布比较锚点；本轮云端基线比较明确使用 9d7fa80。

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

- **S1 实际资源仍缺失。** 需要有来源的结构地图、索引/楼层、门/坐标合同、逐层显示绑定、复核相似组及资源版本/hash。当前清晰显示不可用，不借用 S0；不能仅修改目录就宣称资源可用。
- **名称仍需权威确认。** 当前只显示 S0/S1，不编造赛季中文名。
- **Windows 原生与真实游戏未验收。** 待在独立候选安装环境验证 DPI/字体、无焦点 popup、输入穿透/全局快捷键、准备及运行中锁、隐藏控件独立设置、位置编辑完成/取消、旧配置与真实图库保留、文件选择器、退出后 worker 排空。
- **依赖迁移边界。** 已领养旧包与同字节完整 ZIP 之间的纯依赖变化也明确拒绝，不自动迁移收据。真正文件内容改变的合法旧图升级仍按原事务支持；未知损坏收据不以兼容名义放过。
- **资源实物边界。** 录屏中的具体未知冲突 ZIP 未作为复现输入，本轮只修复已证明的认可旧包与依赖/上下文合同问题；真实包与未知冲突不能宣称全部已修。下载网络与 Windows 文件句柄事务也不由本轮合成测试替代。
- **候选尚未交付。** 没有远端 CI、Setup 构建、安装、合并或发布。后续若获准发布，须对最终同一远端 commit 做联合检查、干净构建与安装验收，另行核对发布范围，不改写 1.0.7 公告。
