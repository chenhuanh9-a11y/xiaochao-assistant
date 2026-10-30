# 开发与交付流程

GitHub 仓库是源码协作与交付的依据。本地完成的改动应及时提交并推送到对应分支，说明变更范围与验证结果；存在未通过的检查或尚未确认的行为时，在 PR 中明确记录。

1. 开始修改前获取远端更新，保留其他协作者的提交；使用普通推送，不强推覆盖他人工作。
2. 在功能或修复分支开发，完成针对性测试和代码复核。检查通过且属于已授权交付范围的 PR 再合并到 `main`；存在冲突、失败检查或新的范围变化时先处理并说明。
3. 打包优先从明确远端 commit 的干净 checkout 开始，记录提交 SHA、显示版本、构建参数和产物校验和。不要夹带未提交的本地源码。
4. 程序与地图资源分开管理。交付记录绑定资源版本、结构/索引格式及哈希，核对程序实际消费的资源；不能只凭文件名推定兼容。
5. 资源发布遵循授权范围和来源说明。游戏原包、账号信息、个人配置、日志、密钥及未授权素材不能随源码或构建产物提交。

离线测试应使用 fixture 或 mock；需要 Windows 输入、GUI 或游戏实测的功能，单独记录其验证状态。测试通过不等同于完成真实游戏验证。

## 变更规模与功能关系

根目录 [AGENTS.md](../AGENTS.md) 和 [PR 模板](../.github/pull_request_template.md) 提供短检查清单。普通小修只需在 PR 说明问题、影响链路、实际验证和未测项；不要为每次修复增加设计报告或跑无关全库。跨模块协议或状态归属变化才补必要设计。以下是当前代码导航，不代表各条链路已经完整统一或全部验收。

| 变更入口 | 必须跟到的消费者 | 现有代码与测试起点 |
| --- | --- | --- |
| 识别、正门/侧门、相似组 | 候选证据 → 组态/身份 → 当前帧验证 → 提交；暂定成员与唯一身份确认分清 | [map_identity_engine.py](../app/map_identity_engine.py)、[difficult_native_identity.py](../app/difficult_native_identity.py)、[test_native_similarity_runtime.py](../tests/test_native_similarity_runtime.py)、[test_front_gate_observation.py](../tests/test_front_gate_observation.py)、[test_side_entry_search.py](../tests/test_side_entry_search.py) |
| 首次/后续贴合、楼层、关闭与重开 | 当前地图/层/帧 → worker → 验证 → round-pose/core → 正确可见像素 | [identityv_daily_assistant.py](../app/identityv_daily_assistant.py)、[fast_structure_alignment.py](../app/fast_structure_alignment.py)、[alignment_pose_transaction.py](../app/alignment_pose_transaction.py)、[test_queued_first_pose_delivery.py](../tests/test_queued_first_pose_delivery.py)、[test_queued_delivery_core_contract.py](../tests/test_queued_delivery_core_contract.py) |
| 作者与自定义路线选择 | 偏好 → 实际来源与每层显示桥 → 渲染缓存 → 当前显示；身份/结构位姿不漂移 | [optional_author_displays.py](../app/optional_author_displays.py)、[test_route_selection_controller.py](../tests/test_route_selection_controller.py)、[test_route_selection_regressions.py](../tests/test_route_selection_regressions.py)、[test_custom_route_integration.py](../tests/test_custom_route_integration.py) |
| 下载/导入资源 | 下载状态与校验 → 安装事务 → 资源工厂 → 作者选项/列表 → 缓存和旧任务失效 | [star_map_download.py](../app/star_map_download.py)、[star_map_import.py](../app/star_map_import.py)、[map_resource_browser.py](../app/map_resource_browser.py)、[test_live_author_import.py](../tests/test_live_author_import.py)、[test_live_author_import_controller.py](../tests/test_live_author_import_controller.py)、[test_map_package_upgrade.py](../tests/test_map_package_upgrade.py) |

噩梦与困难应使用同一识别/贴合及生命周期实现，差异由资源、楼层元数据和必要参数提供。修改前核实际调用路径，修改后用两模式受影响的参数或代表资源验证。无法验证的资源契约要留明确缺口；不能删验证、复制模式专属算法或强套另一模式的门/坐标语义。既有测试名只是起点，例如排队首次任务测试不能代表后续每次开图延迟已经修复。

涉及异步与状态的改动，按风险选择成功、失败、取消、重复触发、迟到回调及换图/换层/关图/结束本局反例。检查最后实际消费者，而不只检查日志、列表条目或中间返回值。可重复的少量代表回归优先；不为小修添加镜像实现的测试或无关全量测试。

Tk 交互沿用持久页面和稳定卡片 ID：重复点击当前页/标签应幂等；普通状态变更只写发生变化的控件，不先隐藏整页/全列表再重新摆放。轮询可以读取最新状态，但状态相同就不重复写 Tk 属性、布局或图片。导入/删除等真正改变资源身份时才失效对应列表与预览缓存，保留其他区域的选择、焦点和滚动。核对卡顿时分别记录回调、布局/绘图调度和实际可见效果；仅缩短回调不能证明流畅。

## 贴合速度与可靠显示的验收口径

首贴属于识别阶段，约 **500ms** 是目标；已锁图且首贴成功后的每一次重新开图，约 **300ms** 是目标。当前规范不宣称任一目标已经达成，也不改变验证门槛。

按同一单调时钟和开图轮次关联：输入接收、画面就绪、截图、楼层高亮判断、锁图后的求解、排队/验证与首次正确可见像素。先把首贴与后续开图分开，再区分算法计算时间和端到端时间；嵌套阶段不能相加，日志终点不是可见像素时不得冒称端到端。无真实按键时刻的回放写明起点是保存帧或事件注入。

连续样本报告版本/资源、模式/地图/楼层、样本数、p50/p95/max、未显示次数、错层次数以及超时判定窗口；百分位写明采用的方法。未显示的轮次不能从统计中无声删除，也不能记作零耗时。错层要对照有来源的图像真值；高亮尚不稳定、缺帧或无真值时单列未知。涉及楼层逻辑时按实际支持范围覆盖 B1、F1、F2 及过渡帧，不把所有模式都假定为三层。

前后对比注明是否同帧、同资源、同运行环境及热/冷缓存。保存帧回放可用于定位和回归，真实游戏的调度、截图就绪、绘图时延与长期 RSS/句柄仍须独立验收，不能从局部算法更快推导整体更稳。

## 合并后与安装包绑定

Windows 主程序的 PyInstaller `EXE` 必须显式指定 `uac_admin=True`。
仅在 XML 中声明 `requireAdministrator` 不够：PyInstaller 会按该参数覆盖执行级别。
主 spec 在收集前读取真实 PE 的 RT_MANIFEST #1，拒绝缺失或非
`requireAdministrator` / `uiAccess=false` 的成品；外部构建脚本必须保留该参数和检查，
并可对最终暂存主 EXE 再运行 `python tools/verify_admin_manifest.py <主程序.exe>`。
该检查只读取资源，不启动 EXE，也不证明 UAC 提示或快捷键已经实测。
安装快捷方式应直接指向主 EXE（工作目录为安装目录），安装完成启动应通过 Shell 执行主 EXE。
普通用户点击后由 Windows 请求 UAC，同意后启动，取消则停止；不得以计划任务或系统策略绕过确认。
导入 helper 使用 `asInvoker`，由主程序启动时继承其权限，不作为独立用户启动入口。

若快捷键仍无响应，最小可见诊断应同时展示显示版本/构建指纹、当前 EXE 入口和当前进程
TokenElevation 的“管理员 / 普通 / 未知”状态；查询失败必须显示未知，不能推定为权限不足。
本次仅修复打包合同，尚未加入该诊断界面；权限正确也不能单凭此断定快捷键故障原因。

合并改变了最终提交或调用关系时，在最终远端 commit 的干净 checkout 上运行受影响的联合检查，然后从同一 commit 构建。若某个未变模块的测试复用旧证据，记录对应文件/哈希和复用范围，不能将合并前的整体通过直接写成合并后通过。

交付回执记录 source commit、显示/数值版本、构建指纹、资源版本和锁文件哈希、实际内置范围、Setup 精确字节数与 SHA256。需要外部资源时明确获取与绑定合同；缺失时报告，不能静默退回旧本地源码/素材。软件只交付 Setup，地图导入包仍可用 ZIP；未生成安装器时不要用便携 ZIP 的大小估算安装包收益。

源码/离线检查、包内模块检查、安装、真实 GUI/游戏和线上发布分别标明结果。生成 Setup 不代表自动授权安装或发布；保留旧交付物和用户数据，未包含的 PR/修复不得写进当前版本公告。

## 通用机制优先复用

下载、取消重试、导入和退出清理等通用机制，先查官方教程及成熟开源实现，再结合现有服务接入；核对版本、许可证与完整状态生命周期。不要只照搬外观、引入无关整套依赖或顺带改写识别贴合算法。相关说明应简记实际参考及适配点，例如 Qt 的 [Window 关闭](https://doc.qt.io/qt-6/qml-qtquick-window.html#closing-signal)、[QCoreApplication 退出](https://doc.qt.io/qt-6/qcoreapplication.html#quit)和 [QThreadPool 生命周期](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThreadPool.html)。
