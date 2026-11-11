# 旧资料迁移核心（尚未完成用户安装流程）

本轮只提供 QtIFW helper 的显式离线入口，未自动扫描用户目录、注册表或外部路径，未接入安装器选择页面。旧/非空安装目录的准入拒绝保持不变。

在两个程序均已关闭、QtIFW 新目标完成程序提取和 verify-program、尚未 install-maps 或启动程序时调用：

```
qtifw-trial-helper.exe migrate-legacy <新安装目标> <明确选择的旧安装根>
```

helper 先验证目标所有权、installing 阶段及完整程序清单，再调用核心。该入口不能对已完成安装补做迁移。复制成功后仍需完成原安装验证；失败保留旧入口，用户可继续从旧 EXE 启动。不会修改快捷方式或卸载注册状态，不能宣称自动恢复 Windows 安装器状态。

核心复制 work/nightmare-memory、difficult-memory、seasons、user-routes；保留设置、图库、旧路线、更新/教程状态以及学习/确认图片原字节。新副本中的 custom-routes.json 继续由已有 CustomGalleryStore._migrate_legacy 转换，不额外实现转换算法。排除 failures、runtime-successes、downloads、事件/错误日志、ZIP、临时与锁文件，不扫描 outputs 和外部目录。

清单位于新 program/legacy-migration.json，记录以相对路径 SHA256 为键的大小、文件 SHA256 及来源路径摘要，不记录绝对路径或原生异常文字。复制到 .legacy-migration-work，每个文件校验后无覆盖发布，最后复验来源和整棵暂存树，再无覆盖重命名为 work。目标 work 已存在即拒绝，不能合并。断点重试复验所有已复制文件；不完整的私有复制片段转存 .legacy-partial-*，不删除。清单的 publishing 状态支持重命名后、清单完成前中断的继续确认。正常异常保留清单；进程崩溃残留 .legacy-migration.lock 或清单 .new 时停止，需人工检查后恢复，不自动猜测恢复。

尚未支持：地图/作者包的成套资源、收据、依赖及历史快照迁移。存在 work/map-packages（包括损坏收据或锁）或已知待恢复事务标记时，整个迁移停止，不切换部分资料。也未支持没有 _internal/installation-profile.json 的早期正式 Inno 安装；不会仅凭 XiaochaoAssistant 名称或 EXE 名称认领身份。正式产品档案必须匹配固定 production AppId，私测拒绝。

调用期间必须保持旧程序关闭；本轮不查询其他进程或窗口。复验能发现普通源文件变化，但不承诺抵抗并发恶意替换、任意断电或文件系统故障。Windows 原生锁/无覆盖重命名、真实安装器交互、首次启动消费者、旧入口回退需真机验收。没有真实旧产品数据/GUI/游戏通过声明。
