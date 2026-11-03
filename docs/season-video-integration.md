# 次日赛季与录屏反馈整合验收记录

状态：仅云端源码候选，未合并、推送、打包或发布。已发布的 1.0.7 与原功能工作树保持不动。这里的“录屏反馈”指录屏指出的交互/资源问题，不代表新增录屏编码能力。

## 完整目标与已实现范围

1. **赛季先于难度。** 主页、地图资源、悬浮控件、设置共用“赛季下拉 → 噩梦/困难”。目录集中声明编号/名称；名称未确认时只显示编号，不猜名称。扩展 S2 的合成测试覆盖保存、重启、浏览和运行态，但不授予其 S0 资源权限。
2. **旧数据与运行边界。** 无赛季字段的旧设置保持 S0；旧图库、未知设置项与已有自定义控件布局保留。运行态在准备、本局运行及已排队开始期间锁赛季/难度；图库浏览独立，不改运行上下文。异步拒绝/迟到结果不能冒充已切换。
3. **独立设置与控件入口。** 定点地图不透明度、位置编辑、界面不透明度直接置于设置，无需展开高级项；控件定制位于快捷键下。隐藏悬浮控件不妨碍从设置完成/取消位置编辑。新默认控件包含模式及本局动作，不重写旧用户布局。
4. **图库直接管理。** 展示页保留旧作者卡片/独立预览，并增加精确绑定 packageId 的删除入口与末尾 ZIP 导入卡。展示页和管理页共用唯一文件框/确认框；打开时冻结目标赛季/删除计划，取消、失败、重复操作与忙碌状态不跳转页面或漂移目标。
5. **认可旧图包恢复。** 完整匹配认可清单、无收据的旧资源可进入原升级事务，不要求用户先手工找文件。只读检查、确认后重验、独占租约、回滚及成功后临时副本清理保持。未知、残缺、被改动或共享占用文件不放宽。
6. **整合发现的补强。** 新增其他包 requiresFiles 精确依赖保护，避免恢复/升级改写仍被另一包要求为旧内容的文件；依赖不是所有权，未改变依赖的其他作者图仍可升级。检查及写入前共用重验，坏收据失败关闭。图库预览由实际字体/控件高度预留说明空间，修复宽窗口底部覆盖说明被裁切 5px。

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

无文本冲突。自动合并涉及 ClientSettings.qml、star_map_import.py、client_resources.py；复核保留两级选择器、独立设置布局、`_require_s0`、season 参数、确认指纹及恢复提示双方意图。没有改版本、Setup spec、发布或资源锁文件。

## 最终云端验证

2026-11-03，Python 3.12.14、PySide6 6.11.1、OpenCV 4.12.0，Linux Qt offscreen/software。最终产品源码为 a51ade53（后续仅此验收文档与现有 CI 分组）。以临时目录、合成声明/PNG/ZIP、受控 native 替身运行，未连接游戏或读取用户安装数据。

逐模块独立进程共 **52 模块、669 项：638 通过、17 跳过、14 失败**。其中直接覆盖本轮赛季、设置、图库、安全恢复与真实列表刷新的 **88 项全部通过**，它们是 669 项的子集，不重复累计。最终 diff whitespace 检查通过。

- 其他包依赖保护的最初 7 项回归先红（含子用例共 9 个失败），正式扩展为 9 项后全绿；验证拒绝后不领养、不覆盖，依赖字节不变时可正常更新，以及确认后依赖变化的再检查。
- 真实 Backend、ResourceService、PackageManagerService 与 SessionService 共 3 项新增服务集成：可选作者导入/删除使真实 profiles、model、预览更新；S1 浏览期间 S0 更新无串季；恢复 S0 保留选择，图片 URL 和解码像素更新。基础作者删除保留缺图声明，但可用数/预览归零。不是 FakeBackend 固定列表，也不是实际 QML 图片渲染证明。
- 2e87ce6 只修复两处已过期测试假设：coordinator applied context 由二元变三元；模式设置现在一次保存赛季与难度。原断言先失败，新断言核对真实新合同，不放宽产品行为。
- 初次探索误用了仓库不存在的 `test_star_map_import` / `test_star_map_download` 模块，产生的两个 loader errors 未计入上述真实测试数；改为仓库现有资源模块后完整重跑。
- 少数旧 Qt fixture 不支持任意同进程顺序组合，独立审查曾遇到退出 139。最终 52 模块按各自进程运行，没有把该探索性崩溃算为通过，也不称任意组合生命周期已解决。

可复跑命令（每个模块单独进程）：

```sh
export PYTHONPATH=app:tests:tools:experiments/qt_quick
export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software
python -B -m unittest -v <module>
```

本轮直接模块：`test_season_settings`、`test_season_catalog`、`test_season_selector`、`test_season_gallery`、`test_season_resources`、`test_season_session`、`test_settings_accessibility`、`test_client_gallery_actions`、`test_client_gallery_layout`、`test_client_gallery_resource_refresh`、`test_legacy_import_recovery`、`test_package_dependency_upgrade`。现有 Windows 联合工作流增加 season-ui 分组及依赖保护测试；未触发远端 CI，不称 Windows 已通过。

### 14 项基线失败

在独立、干净的同基线 9d7fa80 同环境逐模块复跑，失败身份一致：

- `test_client_live_runtime` 2 项 static reference：当前 Linux 的 Windows API 替身/原生能力边界。
- `test_client_control_actions` 1 项、`test_client_control_stability` 9 项：旧控件 fixture/期望问题。
- `test_client_live_controls` 1 项 HWND 复用：fixture 未注入 native binding probe 的 lost 结果，不能凭 catalog 变化替代该合同。
- `test_map_resource_context` 1 项：Python 3.12 Path.resolve() 内部 stat 与测试的 no-stat 替身断言冲突。

这不是全库全绿，也不把基线失败当成不需后续处理的产品保证。

### 离屏截图

最终 UI 源 a50c0f9 重新生成 16 张 PNG；后续 a51ade53 仅新增测试，UI 字节未变。主页 540/1100 宽、图库 540/1000/1200 宽、设置 540/1200 宽和真实无焦点标志的悬浮控件均覆盖。实际鼠标打开下拉/选赛季/选难度；图库宽页覆盖说明完整可见，bottom 与 viewport 均为 347/347 或 597/597。QML warnings 为空。

540×740 设置采用单列，三项显示入口需要滚动，虽不需展开高级项，但不能称首屏可见。某张 S1 图库交互截图使用保留固定目录的旧 fixture，只用于选择器交互，不能作为真实 S1 资源证据。生产 S1 隔离以新增真实 Backend 测试为据。

原始命令、日志、截图、几何与 SHA256 记录保存在本候选的忽略目录 `work/integration-evidence/`，不提交用户日志/素材。最终回归为 `regressions-final/summary.json`，独立新图库模块为 `gallery-refresh-final.log`，基线对比为 `baseline/summary.json`，截图为 `screens/`。

## 未完成与下一步验收条件

- **S1 实际资源仍缺失。** 需要有来源的结构地图、索引/楼层、门/坐标合同、逐层显示绑定、复核相似组及资源版本/hash。当前清晰显示不可用，不借用 S0；不能仅修改目录就宣称资源可用。
- **名称仍需权威确认。** 当前只显示 S0/S1，不编造赛季中文名。
- **Windows 原生与真实游戏未验收。** 待在独立候选安装环境验证 DPI/字体、无焦点 popup、输入穿透/全局快捷键、准备及运行中锁、隐藏控件独立设置、位置编辑完成/取消、旧配置与真实图库保留、文件选择器、退出后 worker 排空。
- **资源实物边界。** 录屏中的具体未知冲突 ZIP 未作为复现输入，本轮只解决已证明的认可旧包无收据问题；真实包与未知冲突不能宣称全部已修。下载网络与 Windows 文件句柄事务也不由本轮合成测试替代。
- **候选尚未交付。** 没有远端 CI、Setup 构建、安装、合并或发布。后续若获准发布，须对最终同一远端 commit 做联合检查、干净构建与安装验收，另行核对发布范围，不改写 1.0.7 公告。
