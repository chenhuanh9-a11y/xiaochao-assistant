# 无路线渲染图的两种资源包

本草稿对应「地图资源」「路线显示」，基线为 main 的 1.1.9 准备提交 `502ec2a`。派工明确记载 huan 已选择两种都做；不再询问这项方向。地图识别、楼层判断、路线贴合、安装与更新的实现及显示授权保持原样。相邻自定义图库继续拥有显式自建路线选择。

## 包与调用接缝

- 制作：`tools/export_rendered_map_packages.py` 从一个已校验的 original 基础包和显示注册导出两包。独立路线包直接使用既有 `export_author_display_component`、display v1 与完整基础引用。附带包重用同一组图片、楼层与矩阵；原结构、坐标、识别、控件文件逐字节复制，只有资源上下文增加显示接口。
- 同包所有权：附带图片属于原 `recognition-base` 组件，入口为 `rendered-display/display.json`，图像在同组件的 `rendered-display/images/`。入口使用 `xingtu-base-rendered-display-v1`，基础依赖为空值，含义是直接由本组件持有；没有自引用、虚假摘要或额外自动启用的路线组件。新能力 `original-qt-rendered-display-v1` 显式限制消费者。
- 准入：沿用 `PackageView` 文件所有权、摘要、命名空间及原图校验。附带与独立形式共用现有作者显示的逐层来源快照、结构版本键、地图、楼层、画布、坐标空间、图片位深、完整解码和仿射矩阵验证；仅同包所有权解析不同。未知 schema/字段、异组件、坏来源、坏画布、非法矩阵及坏图片仍拒绝。
- 实际入口：`ImportedResourceService` → 原导入/自动启用事务 → `client_imported_providers` → `client_original_providers` → 原 `load_native_resources` / `decode_original_base` → `ImportedBaseRenderedDisplay`。没有只改旧共享显示函数。
- 实际消费者：`prepare_display_layer` 在默认结构选择时读取附带图片；已明确选中的可用作者或自建路线先按原流程返回，作者缺层时再用附带图或结构底图。独立包按既有路线导入、作者选项和选择流程使用，名称固定为“无路线渲染图”，不冒充作者，不改写偏好。
- 结果与缓存：新图片层仍经 `LiveRuntime._display_layer`、原矩阵重投影和原帧/本局/楼层/资源代次检查提交给 Qt 图片消费者。颜色缓存最多三层、96 MiB，绑定实际组件及图片路径；每次命中仍重读注册与图片摘要。原资源读取作用域包含新显示所有者；资源切换后旧引擎拒绝返回。没有新增识别或位姿写者、跨层许可或新的显示授权。

空资源库仍没有地图。图片只在外部资源包内，程序和 Setup 不增加地图；不修改安装器、版本或发布配置。

## 兼容矩阵

| 程序 | 资源形式 | 行为 |
| --- | --- | --- |
| 1.1.9 | 既有 original 结构与路线包 | 原读取与逐层兼容规则继续适用 |
| 本草稿 | 既有 original 结构与路线包 | 原行为；没有附带图的楼层仍用结构底图 |
| 本草稿 | 附带渲染图的新结构包 | 正常导入并自动启用；无可用选中路线时读取附带图 |
| 1.1.9 | 附带渲染图的新结构包 | 归档可保存，但缺少声明能力而拒绝启用，不进入可用上下文；不声称旧程序已有此默认入口 |
| 1.1.9 / 本草稿 | 独立渲染图路线包 + 对应基础组件 | 原 display v1 导入、选择与最终图片消费；按 #168 对当前启用版本逐层核对 |
| 任一程序 | 独立包但没有对应基础组件 | 拒绝启用，不借入制包时的旧基础、不静默换组件 |

附带形式的最低要求是支持上述新能力的客户端，没有擅自指定正式版号。只要实际结构、来源和坐标兼容，独立包可跨对应基础组件版本使用；不恢复纯版本号或发布摘要相同才允许的旧限制。

## 素材与覆盖

仅从既有明确交付的四个 original 结构包，沿其结构产品内的原制图凭据读取图片，没有扫描用户磁盘找图。S0 噩梦使用结构元数据记录的原渲染文件；S0 困难使用同一原制图目录中的彩色渲染图；S1 使用原生产脚本及其 geometry 凭据指向、且摘要匹配的 room render。S1 的后续显示副本存在额外图层，未拿它冒充原图或改写结构摘要。

| 资源上下文 | 声明层数 | 提供渲染图 | 缺图 |
| --- | ---: | ---: | --- |
| S0 噩梦 | 78 | 78 | 0 |
| S0 困难 | 60 | 58 | 第 29 图的 1、2 层，仅历史结构，无对应 typed 渲染来源 |
| S1 噩梦 | 60 | 60 | 0 |
| S1 困难 | 56 | 56 | 0 |

共 252/254 层。每层核对实际彩色像素、画布、原始渲染到结构矩阵及 alpha 轮廓；有原图片摘要的同时核对摘要。没有将 clean_mask 改名，也不填造缺图。两种形式使用完全相同的 PNG 字节和配准；本轮不再裁切或缩放。

本机完整来源、原包及产物字节数/SHA256、逐层凭据和第一次来源检查的缺口保存在本任务 `work/real/`；图片、原始游戏资料与本机绝对来源路径不提交进源码仓库。产物是地图导入包，不是软件安装包，也未发布。

## 本轮验证

Windows、离屏 Qt；命令环境为 `PYTHONPATH=experiments/qt_quick;app;tools;tests`、`QT_QPA_PLATFORM=offscreen`。临时目录也放在本任务工作目录内。

- `python -m unittest test_original_qt_resource_flow test_export_author_display_component test_export_author_display_component_modes test_imported_author_display test_imported_resource_packages test_client_author_base_compatibility test_client_imported_author_joint_activation test_client_route_preferences test_client_custom_display test_client_route_reprojection -q`：108 项，106 通过、2 项因当前系统不允许创建符号链接而跳过。
- `python -m unittest test_rendered_map_packages -q`：本功能的实际导出/导入、默认最终像素、独立包按需选择、显式作者和自建路线优先、重新构建工厂、兼容版本切换、空资源、缺层、错地图/楼层/坐标、非法矩阵、损坏图片/摘要、缓存与旧引擎拒绝。Qt 的真实 `LiveImages` 读取像素；仿射重投影核对独立矩阵预期并保持原 canonical 输入。
- 生命周期用原有合成采集、楼层观察与求解夹具，显示仍读取真实导入包：延后作者重绘，经过关图、实际选择另一图、改楼层目标和结束本局后，旧请求不能恢复图片。只打开选图器按原规则保留当前路线，不冒称该动作已经换图；冲突楼层目标仍按原证据拒绝。
- 实际四模式八包已生成。新代码的四个附带包经真实 Service 自动导入/启用，252 层通过原工厂、`LiveRuntime._display_layer` 及真实 Qt 图片提供器，逐像素、逐矩阵核对原图。原 main 1.1.9 的四个旧结构包及四个独立渲染路线包也经其未修改的 Service 导入/启用，252 层完成同样的最终像素核对；新建工厂读取保存后的 catalog，不注入导入对象或图像缓存。
- 原 main 1.1.9 的真实 Service 对四个附带包均报告 `resource-client-capability-unsupported`：保存归档但不启用；空库中的四个模式仍没有基础组件。检查脚本最初把完成通知一概当成成功，随后按“已保存但未启用”的实际协议修正，原失败日志保留，没有修改产品或放宽能力检查。

上述是合成流程、实际文件/Service 和离屏 Qt 证据，未启动星图原生界面、游戏、输入或采集，也未安装/构建 Setup。它们不证明实机体验或玩家验收。

## 重叠与验收边界

与 #168/main 的实际重叠为 `app/imported_author_display.py`（复用逐层验证）、`app/imported_original_base.py` 和 original 消费工厂、`client_route_preferences.py`。保留 #168 按当前启用的同组件基础逐层核对的规则。与结构包规范重做潜在重叠为基础上下文接口、新能力声明、文件归属和制作入口；本草稿没有改变 base/controls 布局，也没有向 #142/#149 的旧整合分支推送。以后统一整合须在同一最终源码上复测导入事务、作者选择、基础切换、缓存及最终图片，不能直接套用本分支结果。

单元/合成包、实际资源 Service、离屏 Qt 图片、原生 Windows 界面、游戏与正式发布分别记证据。当前仅交草稿，不操作游戏、不覆盖安装、不合并或发布；具体命令与结果随完成检查更新在 PR 中。
