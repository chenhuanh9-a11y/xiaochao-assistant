# 作者资源管理

资源管理读取本机导入回执，不依赖在线下载目录。在线目录不可用仍允许离线 ZIP 检查和本机资源管理。

- 已安装：回执及文件校验一致；可更新：本地完整但在线 ZIP 哈希不同；需修复：回执存在而文件缺失或字节与记录不同（包括用户编辑）；本地检查未完成：检查取消、文件竞态或无有效结果；旧资源待核实：有作者图片但没有可信所有权回执。
- 移除入口位于地图资源页的“管理已安装资源”，按钮为“备份移除…”。先后台核对精确文件和回执，再显示作者、文件数量、本地改动数量及备份说明，用户明确确认后执行。该操作不判断本地改动是损坏还是用户编辑。
- 只移动回执管理且落在作者显示图像白名单内的普通文件。内容与旧回执不同也可原样备份，不直接覆盖或删除改动。结构、识别、预制数据、作者元数据 sidecar、编辑器和自定义路线保留；同目录的未列文件也保留。
- 确认同时绑定回执 hash 和实际文件/缺失清单的 `planSha256`。确认后文件新增、消失或字节改变，须重新检查确认，不能沿用旧计划扩大移除范围。
- 删除需要未开始对局且后台资源任务空闲，沿用热导入的唯一写入租约。成功后在 UI 线程重建资源 factory、失效预览与作者选项缓存；不用重新启动程序。
- 文件移至安装目录 `work/map-package-removals` 下的独立备份。普通异常和提交前取消尝试回滚；回执最后移动是提交点。外部写者造成回滚目标冲突时，保留备份并报告失败，不覆盖新文件；这不是对任意外部写者的整目录原子事务。**尚无自动 restore 命令或断电/进程被杀后的恢复日志，不声称这类中断可自动恢复。**
- 备份用于人工核对、取回原字节。需要手动恢复时应先退出软件，核对备份与当前文件，不能把备份整目录覆盖到后来已更新的资源上；旧回执的 hash 仍描述原版本，不会把本地改动伪装成校验成功。日常修复可在备份移除后，通过现有严格导入重新安装适用资源包。
- 未登记文件不推断所有权、不自动覆盖。若用户有与现物一致的原版本 ZIP，现有完整校验的重复导入可登记回执；新版字节冲突不适用这个方法，需另外审定迁移方案。

## 主控接缝

后台模块 `author_package_management` 提供：

1. `managed_author_packages_snapshot(root, cancelled=None)` 返回本机四类作者状态列表。
2. `inspect_author_package_removal(root, package_id)` 返回精确移除计划、`receiptSha256`、`planSha256` 及本地改动文件列表。
3. `remove_author_package_live(root, package_id, application_idle=True, expected_receipt_sha256=..., expected_plan_sha256=..., cancelled=...)` 验证确认绑定后移除。
4. `package_inspection_message(error)` 为离线校验错误提供明确消息。

页面调用主控 `managed_author_packages_snapshot(refresh=False)`，该 API 必须只返回缓存状态，不在 Tk 线程逐文件哈希。返回 `{stage, busy, packages, ready, message}`；`refresh=True` 发起后台扫描。

页面 `inspect_author_package_removal(package_id)` 异步产生 `ready`；`confirm_author_package_removal(package_id, expected_receipt_sha256=..., expected_plan_sha256=...)` 将两项确认传入同热导入写入租约，完成后通过生产事件队列回到 UI 线程刷新。旧代次事件不得覆盖新状态。后台扫描、计划和移除异常应成为状态消息，不能逃逸 UI 事件循环。

主控已接入上述后台任务、确认和资源刷新链。以下验证不等于完整应用或游戏验收。

## 定向验证

`tests/test_author_package_management.py` 使用临时安装目录与真实 ZIP/OS 写锁，覆盖四包首导/重导/备份移除/再导入、核心与自定义数据保留、取消/IO异常回滚、改动文件原样备份、未登记拒绝、确认后文件或回执变化、Windows reparse 属性反例。

`tests/test_author_package_management_ui.py` 执行实际页面方法，窗口为无输入替身，覆盖状态、在线目录独立性、显式确认绑定、busy 状态和关闭后不再更新。

`tests/test_map_browser_page_lifecycle.py` 另以真实 withdrawn Tk 按钮回调验证备份移除与重导入刷新。`tests/test_download_cache_recovery.py` 使用真实 Windows 句柄锁和隔离改名，但 HTTP 响应为受控字节，未验证真实网络下载。PR54 合并后70项断言通过，组合运行出现一次 Tk 图像析构线程警告；单独9项复跑未复现不能抹去该警告，也不能据此声称 GUI 稳定性通过。

## 已核实的旧内置作者资源接管

旧安装器可能保留早期作者文件，但这些文件没有通过导入器安装，因此没有 `work/map-packages` 收据。资源管理后台扫描现在可登记四套 **20261027 已交付原包** 的完整作者显示集合。登记不会改动图片，不需要重启，也不会把缺少记录描述为文件损坏。

信任源是 `tools/build_legacy_author_inventory.py` 固定的四个原 ZIP SHA256。生成器先核 ZIP 整体 hash，再核原 manifest 中每个作者显示文件的长度和 hash，输出 `app/legacy_author_inventory.py`（只含路径/hash/字节数，不含图像或私人路径）。按 `python tools/build_legacy_author_inventory.py SERVER_STAGING` 可重建。清单区分 `author-image` 与 `author-sidecar`；requiresFiles、结构、预制及自定义路线从未纳入。展十原包 171 项仅允许其中 90 张作者图，另外 81 项保留核心资源身份。

后台沿原导入器写锁验证完整集合，排除路径链接、共享归属和正在升级的安装；用完整临时收据的独占发布避免覆盖其他写者。已有收据优先，不重写。部分缺失、任一字节不一致或不认识的旧版本保持“旧资源待核实”，保留原文件；没有从当前现物反推可信 hash 的机制。未知邻居文件也不纳入收据。

自动接管规则仍要求与可信旧包的完整显示集合一致。接管后，确认备份移除流程可保留后来改动过的已登记图片，刷新列表/作者选项；再导入新版包仍复用已有免重启流程。直接覆盖不同版本仍遵循原升级合同；可先备份移除已管理作者包，再免重启导入新版。识别和贴合算法不因该恢复流程改变。

验证使用隔离安装副本及真实四套旧包，不修改玩家目录。未进行 GUI/游戏操作；进程强杀、断电期间的恢复不属于本次验证承诺。
