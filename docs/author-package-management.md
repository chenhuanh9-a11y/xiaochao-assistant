# 作者资源管理

资源管理读取本机导入回执，不依赖在线下载目录。在线目录不可用仍允许离线 ZIP 检查和本机资源管理。

- 已安装：回执及文件校验一致；可更新：本地完整但在线 ZIP 哈希不同；需修复：回执存在而文件缺失或损坏；本地检查未完成：检查取消、文件竞态或无有效结果；旧资源未登记：有作者图片但没有所有权回执。
- 删除入口位于地图资源页的“管理已安装资源”。先后台核对精确文件和回执，再显示作者、文件数量及备份说明，用户确认后执行。
- 只移动回执管理、哈希仍相同且落在作者图像/精确 sidecar 白名单内的文件。结构、识别、预制数据、编辑器和自定义路线保留；同目录的未列文件也保留。
- 删除需要未开始对局且后台资源任务空闲，沿用热导入的唯一写入租约。成功后在 UI 线程重建资源 factory、失效预览与作者选项缓存；不用重新启动程序。
- 文件移至安装目录 `work/map-package-removals` 下的独立备份。普通异常和提交前取消尝试回滚；回执最后移动是提交点。**尚无断电/进程被杀后的自动恢复日志，不声称这类中断可自动原子恢复。**
- 未登记文件不推断所有权、不自动覆盖。若用户有与现物一致的原版本 ZIP，现有完整校验的重复导入可登记回执；新版字节冲突不适用这个方法，需另外审定迁移方案。

## 主控接缝

后台模块 `author_package_management` 提供：

1. `managed_author_packages_snapshot(root, cancelled=None)` 返回本机四类作者状态列表。
2. `inspect_author_package_removal(root, package_id)` 返回精确移除计划和 `receiptSha256`。
3. `remove_author_package_live(root, package_id, application_idle=True, expected_receipt_sha256=..., cancelled=...)` 验证确认绑定后移除。
4. `package_inspection_message(error)` 为离线校验错误提供明确消息。

页面调用主控 `managed_author_packages_snapshot(refresh=False)`，该 API 必须只返回缓存状态，不在 Tk 线程逐文件哈希。返回 `{stage, busy, packages, ready, message}`；`refresh=True` 发起后台扫描。

页面 `inspect_author_package_removal(package_id)` 异步产生 `ready`；`confirm_author_package_removal(package_id, expected_receipt_sha256=...)` 进入同热导入写入租约，完成后通过生产事件队列回到 UI 线程刷新。旧代次事件不得覆盖新状态。后台扫描、计划和删除异常应成为状态消息，不能逃逸 UI 事件循环。

当前这份资源/UI变更不修改主控文件；主控 owner 接入后才算完整功能可运行。未做真实 GUI 或游戏测试。

## 定向验证

`tests/test_author_package_management.py` 使用临时安装目录与真实 ZIP/OS 写锁，覆盖四包首导/重导/移除/再导入、核心与自定义数据保留、取消/IO异常回滚、改动文件与未登记拒绝、确认后回执变化、Windows reparse 属性反例。

`tests/test_author_package_management_ui.py` 执行实际页面方法，窗口为无输入替身，覆盖状态、在线目录独立性、显式确认绑定、busy 状态和关闭后不再更新。
