# 有限自动失败证据

自动失败采样默认关闭，独立于原有手动“包含失败证据”开关，可随时关闭。仅在当前几何验证失败或地图准备超时后复用采集线程已经返回的绑定游戏客户区帧；不会触发额外截图、桌面采集、输入、游戏操作或上传。

生产入口调用共享 `structure_current_map_evidence`，从同一帧的实际地图连通区域取得裁剪范围，并去掉外围 8 像素提取边距。裁剪来源与 bounds 写入元数据。无法可靠提取地图、没有合法当前帧、绑定或代际已变时，不保存原始客户区替代图；缺失原因明确记录/回传。这个裁剪用于减少外围昵称、聊天和 HUD 被保存的机会，并非对真实游戏画面内容的匿名化保证。

每个进程最多保存 6 张自动地图裁剪图，同一模式/失败原因至少间隔 60 秒；独立自动目录内最多保留 6 张、合计 12 MiB 图片。自动元数据最多 24 条、256 KiB。自动图片与元数据保留 7 天，在后续采样和导出诊断的已有工作线程内清理过期内容。程序未运行时不会启动清理任务。清理仅触及 `failures/automatic/auto-[32位hex].png` 和自动 journal，不清理手动样本。

`LiveRuntime.set_automatic_failure_sampling_enabled(bool)` 控制自动采样；原有 `set_failure_sampling_enabled(bool)` 保留手动兼容行为。自动状态事件为 `automatic-failure-sample`，携带 `failureSample.saved/missingReason/limits`。启用自动采样不授权导出旧手动整客户区样本；自动证据导出也需独立显式选择；导出调用 `export_failure_evidence(..., automatic_only=True)` 仅选择自动证据。用户显式选择手动证据时才使用兼容的 `automatic_only=False`。

`failure_sampling.cleanup_automatic_failure_samples(root, now=None)` 可在不采样时独立清理。测试使用临时目录和合成像素验证生产 dispatch → 同帧 guard → 地图裁剪 → journal，以及精确像素 ZIP 导出、限额和手动证据隔离；未执行真实游戏或 GUI 验收。

自动存储的清理、写入和导出读取共用根目录事务锁：进程内非阻塞锁及跨进程文件锁。锁忙立即返回明确状态，不在 UI 线程等待。配置、切换开关和 shutdown 仅改变内存状态，不执行扫描、哈希、删除或 fsync。归属不明、文件硬链接或校验失败时，自动导出拒绝读取图像；旧手动导出保持原有独立行为。

图片持久化使用同一根级锁内的 prepare / commit 协议，而不是把 PNG 和普通 journal 当成一次原子写入。PNG 创建前，先持久化 `automatic-failure-image-intent`，包含精确相对路径、字节数、SHA256 和完整诊断行；PNG 写入并 fsync 后再提交普通样本行。intent 与已有行合计仍限制为 24 条 / 256 KiB。后续采样或导出清理只恢复一个 intent：图片缺失则取消 intent；完整图片的大小、哈希、路径和单链接全部吻合则完成提交；部分写入、内容变动、硬链接或非法路径则原样保留并返回明确恢复失败原因，不凭文件名删除未知数据。不存在无限恢复扫描或新后台线程。

回归通过真实 `record_automatic` 故障注入复现旧问题：PNG 写入后、最终 journal 提交前中断，旧实现下一次录制返回 `ownership-unavailable`。新增进程测试在该提交点执行 `os._exit(73)`，确认重启后的清理将有效 intent 恢复为已提交样本，PNG 字节保持不变；另验证缺失图片取消 intent、变动图片与匹配哈希的硬链接图片保留。这个验证覆盖进程中断的完整 PNG 窗口；未声明验证操作系统断电、磁盘损坏或所有原子替换临时文件的中断窗口。

原子 journal 替换中断可能保留 `.automatic-journal-[8位临时字符].tmp`。这类文件只按文件类型和大小计入辅助文件预算，不读取内容、不删除、不导出，也不当成图片候选；未知用户文件即使同名模式也原样保留。在目录总枚举最多 128 项的边界内，最多容纳 4 个此类遗留辅助文件，合计 256 KiB；超限明确返回 `automatic-auxiliary-count-limit` / `automatic-auxiliary-byte-limit`，不继续写入或清理。报告给出 `retainedAuxiliaryCount/retainedAuxiliaryBytes`。进程故障测试在 mkstemp 写完、os.replace 之前直接 `os._exit(74)`，确认有效图片 intent 可恢复且遗留辅助文件和未知哨兵均保持原字节；故障前的测试会返回 ownership-unavailable，修复后不再因此阻断采样。
