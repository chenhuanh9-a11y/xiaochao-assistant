# 有限自动失败证据

自动失败采样独立于原有手动“包含失败证据”开关，可随时关闭。仅在当前几何验证失败或地图准备超时后复用采集线程已经返回的绑定游戏客户区帧；不会触发额外截图、桌面采集、输入、游戏操作或上传。

生产入口调用共享 `structure_current_map_evidence`，从同一帧的实际地图连通区域取得裁剪范围，并去掉外围 8 像素提取边距。裁剪来源与 bounds 写入元数据。无法可靠提取地图、没有合法当前帧、绑定或代际已变时，不保存原始客户区替代图；缺失原因明确记录/回传。这个裁剪用于减少外围昵称、聊天和 HUD 被保存的机会，并非对真实游戏画面内容的匿名化保证。

每个进程最多保存 6 张自动地图裁剪图，同一模式/失败原因至少间隔 60 秒；独立自动目录内最多保留 6 张、合计 12 MiB 图片。自动元数据最多 24 条、256 KiB。自动图片与元数据保留 7 天，在后续采样和导出诊断的已有工作线程内清理过期内容。程序未运行时不会启动清理任务。清理仅触及 `failures/automatic/auto-[32位hex].png` 和自动 journal，不清理手动样本。

`LiveRuntime.set_automatic_failure_sampling_enabled(bool)` 控制自动采样；原有 `set_failure_sampling_enabled(bool)` 保留手动兼容行为。自动状态事件为 `automatic-failure-sample`，携带 `failureSample.saved/missingReason/limits`。默认开启自动采样不授权导出旧手动整客户区样本；导出调用 `export_failure_evidence(..., automatic_only=True)` 仅选择自动证据。用户显式选择手动证据时才使用兼容的 `automatic_only=False`。

`failure_sampling.cleanup_automatic_failure_samples(root, now=None)` 可在不采样时独立清理。测试使用临时目录和合成像素验证生产 dispatch → 同帧 guard → 地图裁剪 → journal，以及精确像素 ZIP 导出、限额和手动证据隔离；未执行真实游戏或 GUI 验收。

自动存储的清理、写入和导出读取共用根目录事务锁：进程内非阻塞锁及跨进程文件锁。锁忙立即返回明确状态，不在 UI 线程等待。配置、切换开关和 shutdown 仅改变内存状态，不执行扫描、哈希、删除或 fsync。归属不明、文件硬链接或校验失败时，自动导出拒绝读取图像；旧手动导出保持原有独立行为。
