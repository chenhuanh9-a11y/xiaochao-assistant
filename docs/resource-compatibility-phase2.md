# 作者显示资源快照：第二阶段

基线：PR #101 `d3e741c75d5d34ccc07fbce0965724069d763efc`。本阶段只实现 V2 作者显示包的不可变版本与激活记录；没有把基础识别资源改成新的下载格式，没有引入外部项目代码。这里的“不可变”指应用不原地重写旧版本，不代表用户不能修改磁盘文件或元数据具备数字签名。

## 实际入口与存储

- Qt 的本地与在线 V2 导入使用 `versioned=True`。旧格式包继续走已验证的原事务；旧 API 默认语义不变，但已受快照管理的 ID 拒绝退回平面写入。
- ZIP 格式、V2 坐标契约不变。安装布局为 `work/map-packages/author-snapshots/versions/<id>/<snapshot>/`，其中是排序后短文件名的原始图片/绑定、逻辑路径清单与来源 ZIP 摘要。
- 每次成功导入生成新版本，`active/<id>.json` 指向唯一快照，包含赛季、manifest 摘要、激活代际。相同完整归档且所有已安装字节仍一致时才判重复。
- 同 ID 可删层、增层、PNG/JPEG 换格式以及改变依赖声明。预览列出删层、格式变化及不可用层。旧版本和未声明文件不删除。赛季不得在同 ID 下改变，包括损坏旧版本和停用记录。
- 旧 V2 平面回执只有通过完整身份/字段校验才可迁移。迁移只把原回执放入保留事务，原作者图片、绑定、用户修改的内容与 inode 保持原样。未知或畸形回执阻断，不把 ZIP 清单冒充旧所有权。

## 逐层兼容与能力边界

逐层重新核对图片哈希/尺寸/编码、权威底模解析、坐标绑定、reference/native 文件哈希及精确依赖闭包。只有全通过的层进入运行时目录；其余层显示具体原因并保留文件。

| 条件 | 结果 |
| --- | --- |
| 有效且依赖匹配 | 该层可显示，仍要求当前帧几何贴合 |
| 混合包的 S1 普通层 | 停用；困难/噩梦分别核验 |
| 缺底模、底模版本不同、坐标绑定不同 | 只停用受影响层 |
| 安装后图片缺失或改动 | 只停用受影响层，不覆盖改动 |
| 导入归档图片/清单损坏、重复或越界路径 | 整次导入拒绝，原激活记录不变 |
| 已安装绑定或版本清单整体损坏 | 整个作者不可用，不退回遗留平面副本 |

作者层从不取得识别身份、结构验证或跨层投影权。底模原文件恢复为匹配内容后可重新核验；没有自动迁移仿射矩阵、伪造映射或把不同结构视为相同结构。

## 事务与恢复

1. 共享资源写锁与 UI 空闲租约内重查用户确认的计划。计划绑定归档 SHA、旧回执/激活记录的 SHA 和文件身份、逐层状态及差异。
2. 独占创建新版本并完整校验、fsync。发布前重查底模状态及旧记录，改变则要求重新预览。
3. 完整私有 pending 见证硬链接发布；它绑定根目录物理身份、事务 ID、不可变计划摘要。不得认领同字节但身份不同的外来 pending。
4. 所有目标和祖先类型预检后，把旧激活记录/旧回执移至事务保留区，再以硬链接发布新激活记录。每一步校验已知内容和身份。
5. 完整私有提交结果见证发布为 `committed.json`，这是提交边界。可变 journal 状态被降级为 prepared 也不能回滚已提交新版本。提交后清 pending 失败只允许核验并完成清理。
6. 中断恢复通过既有启动确认、独立恢复 helper、关闭原进程与资源锁执行。预览复用全量回滚预检；未知目标、链接、损坏记录、缺失必要见证均在删除前阻断，保留 pending 与备份。恢复不递归删除版本内容。

运行时视图固定激活代际，每次文件读取重新检查。缓存注册身份含激活 SHA；更新后原 UI 资源生命周期使缓存和迟到任务失效。失败与取消只撤回未提交的受控变更，提交后的取消不能被报告为撤销。

## 管理页面与升级清理接缝

- 管理页面可查看逐层状态，单独停用快照 ID。停用只发布 tombstone，不删除图片或历史版本；提示明确不释放磁盘空间。
- 当前不提供快照一键回退、历史版本垃圾回收或批量彻底删除。含快照的“清理全部图包”会明确阻断，不能把停用冒充已删除。旧式包仍使用原删除事务。上述限制是尚未完成项，不是对原需求完成的宣称。
- 用户要求的覆盖安装清理由独立 `resource_upgrade_reset`/Setup 实现。所有快照、激活记录、保留事务都在其完整盘点的 `work/map-packages` 范围内，确认后一起隔离；不是启动时补清理。
- 新 `pending-author-snapshot.json` 加入共享启动/写入阻断和底模修复阻断。合并升级清理时必须保留双方 pending 项，不能覆盖其 verified Setup lease 或 targetIdentity 返回字段。未完成快照事务应先恢复，不能让安装器无声忽略。
- 自定义路线、设置、实际用户目录均未用于测试。快照命名不授予外部数据删除权限。

## 验证与未测项

合成 fixture 通过生产导入、读取、恢复与队列式 Qt 服务；覆盖同 ID 删层/换格式、单层损坏、旧回执迁移、重复、停用、取消、故障注入、外来文件/复制 marker、状态降级、缺失见证和恢复计划改变。Qt 测试只替换临时目录的产品/实验根分类，不替换归档、文件系统、资源锁或事务验证。

Linux 测试不等于原生 Windows Setup、打包产物或真实游戏验收。需要集成最终 commit 后 Windows NTFS/长路径/实际 helper 回归。磁盘耗尽与外部任意并发改动只能在可检测边界失败关闭；不承诺对能同时篡改全部本地证据的攻击者提供防篡改保证。没有官方签名图包体系，也没有底模不可变分发或跨底模自动迁移。

### 本次本地执行记录

- 核心组：`test_author_resource_snapshots test_generic_author_package test_mixed_retired_author_package test_generic_author_affine test_generic_author_windows_paths test_resource_transaction test_map_package_upgrade test_map_package_removal test_s1_normal_resource_retirement test_generic_author_startup test_managed_base_resources test_managed_base_absent_rollback test_map_package_summary`。新快照测试另包含 32 个观测边界的 64 次前/后崩溃注入。
- Qt 三组分别单独进程执行：`test_client_author_snapshots`、`test_client_resources`、`test_client_package_manager`，避免 Qt 全局实例污染。
- 恢复相关组执行 42 项，其中 40 项通过、2 项错误，不能称全绿：`test_star_map_recovery_helper` 的 Windows STARTUPINFO 替身在 Linux 不存在；`test_client_resource_recovery_lease` 的旧 SimpleNamespace fixture 缺 `_preparation_context_only`。在未修改的同一 `d3e741c` 独立工作树逐项复现完全相同错误，本次没有静默跳过或放宽生产逻辑。
- 只读独立安全审查发现并复核关闭了外来 pending 认领、pin 初次 capture、损坏 journal 回滚与提交状态降级等问题。之后补 POSIX FIFO 非阻塞 metadata 打开负例。没有把合成检查宣称为真实游戏或原生 Windows 通过。
