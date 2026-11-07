# 作者历史控制与底模迁移边界

## 本次实现

基线为 `642c472f5c1ee45735dff4ce83f20d8dc8764de0`，在独立功能分支开发，不改变当前安装器清理、锁图与贴合分支。只使用合成资源测试，不读写用户安装或真实地图。

资源页新增作者图“历史版本”，可以选择保留版本，核对后确认回退。仅切换作者显示激活记录，生成新 generation。当前底模、程序、自建路线、设置、历史图片和事务证据都不改动。仍使用现有 ZIP 协议，不给图包新增自行声明几何可信的权利。

`app/author_snapshot_history.py` 提供三个入口：

- `list_versions(root, package)`：读取安装根绑定的提交记录和清单，列出当前、保留及无完整证据的目录。列表不宣称图片完整或兼容；未知目录不可回退。
- `inspect_restore(root, package, source_transaction_id)`：核对候选全部自有字节、当前底模依赖和逐层绑定，生成绑定当前激活、来源见证、载荷观察及层结果的 `planSha256`。零兼容层拒绝；部分兼容明确列出停用层。
- `restore_version(..., application_idle=True, expected_plan_sha256=...)`：共享写锁和空闲会话下重算计划，确认未变后通过现有激活事务切换。确认不能跨越当前版本、候选或底模变化复用。

## 信任与恢复

1. 只有本安装根的已提交事务才能提名历史版本。核对 journal、保留 pending 指针、新激活 pin、committed/commit-pointer 硬链接见证，以及快照 manifest。复制的见证、外来根、未提交孤儿、缺失或损坏记录不自动获得回退资格。哈希是内容证据，不是数字签名。
2. 候选全部作者文件必须仍为原字节；不能把损坏图片当作“局部兼容”回退。底模不兼容可以逐层停用，至少一个当前兼容层才允许确认。现有激活索引损坏时不覆盖猜测，应先恢复或重新导入。
3. 有界无链接常规文件读取，同时核对句柄身份、大小、mtime/ctime。Windows 时间观察保持 `open + fstat` 同一语义，不混用 Path.stat 的 birthtime。计划后再读、提交前再读全部候选与依赖，并复查最终文件观察。
4. 复用 `pending-author-snapshot.json`、原 journal 及硬链接提交见证，不引入另一种可绕过的锁或恢复 marker。提交前再次核对已发布激活、新 pin 和旧备份；外来同字节新 inode 也不能被认领。异常只在现有整体回滚预检通过后恢复，未知对象保留且 pending 阻断新操作。
5. committed 是唯一提交边界。提交后清 pending 失败只完成核验/清理，不回滚已提交版本；提交前中断恢复旧激活。程序正常写者由共享锁串行化；不能声称防住任意外部进程在最后观察之后进行的并发替换，OS 级 TOCTOU 仍存在。
6. 升级 reset 仍整批隔离已确认旧资源和图包。历史目录、激活和事务见证都在既有盘点范围内，pending 双向阻断保持。已隔离的旧目录不会被本入口自动重新激活；Setup 回滚恢复同一文件身份后，历史证据可继续验证。这里不是安装器或程序版本降级工具。

## 从入口到消费者

- QML 持久页面打开历史列表，后台工作线程读取；请求期间保持现有空闲租约，确认保留租约，取消释放。
- 关闭历史/离开页面只撤销读取及尚未确认的回退，不取消已接受的写事务。迟到历史结果不会重新打开弹窗或弹出无效确认。离页时只关闭属于历史回退的确认，不影响其他资源确认。
- 成功后核对实际声明清单和历史，再发布资源身份变化。生产 client 的双向通知回声不会清空刚核对的历史或重入覆盖完成通知。错误/取消后的后续清单读取保留真实终态提示。
- 提交后取消列表核对明确显示“作者版本已切换”，不说回退被取消。pending 不报成功并继续占用恢复租约。
- 原资源变化链负责作者选项、provider、显示缓存和后台任务失效。快照读者仍检查 activation generation；已热缓存的旧版本像素也拒绝继续显示。回退不改变结构身份、结构位姿、楼层或当前帧几何要求。

## 尚未实现与安全依赖

### 历史版本清理

本次没有垃圾回收，也不释放历史版本磁盘空间。下一步不能递归删除 `versions`：需要独立确认计划，排除当前激活和所有 pending 引用，完整验证文件所有权/改动，未知内容保全，再整批移动到可恢复隔离目录。只有持久提交后才按用户明确选择清理隔离副本，恢复路径需验证对象身份和目录骨架。现有成功快照事务的保留见证是回退证据，不能先删除再宣称可回退。

### 底模版本并存

当前底模解析依赖一个固定安装根；共享结构 reference/context 注册表按地图/楼层唯一，S1 显示 provider 也绑定当前根。只给文件夹加版本号不能形成安全并存，放宽 sourceVersion 或结构版本检查同样不可行。

可独立分三步推进：

1. 建只读底模库存接口 `inspect_base_version(registered_root, expected_inventory)`，只接受程序登记来源，验证完整依赖闭包并返回 bundle/inventory 哈希、场景、结构版本、reference 哈希和坐标域。区分文件完整、程序登记和当前身份权威，不把目录自报内容当权威。
2. 扩展 provider/context/cache 键携带明确 bundle ID，保留一个当前识别底模；旧底模只能作为有绑定证据的显示解释依赖。先验证跨 provider、每层桥及缓存代际，不以给 resolver 填多个根代替实现。
3. 两端完整库存及可复核跨版本映射齐全后，增加只读迁移计划，再决定新作者快照写入事务。旧版本与新版本需要各自完整依赖，不能拼包借层。

### 跨版本可信迁移

只允许程序明确登记且复核的单跳、同赛季/模式/地图/楼层映射。映射同时绑定新旧库存/reference 哈希、坐标空间、画布和复核证据，不根据外观相似、相同尺寸、别名、反向推断或链式传递授权。显示组合应明确区分 `旧 reference → 新 reference` 与 canonical→reference，不能把两个矩阵混为同一域。

既有 `nightmare_author_coordinate_adapter.recompute_migration_audit` 证明的是已知显示/canonical 变换代数一致性，不授权任意 structureVersionKey 跨版本；`native_static_corner_registration` 是带限制的候选证据，也不是通用作者图迁移授权。任何迁移仍不得提供身份、跨楼层或当前帧位姿权威。

目前缺少完整可读旧/新生产底模库存、绑定两端哈希的已复核映射、真实对应点/像素真值及授权当前识别版本名单。因此本次不宣称生产底模并存或跨版本迁移完成，合成测试也不能替代这些输入。

## 验证口径

新核心测试 `test_author_snapshot_history` 覆盖列表、来源/根/见证、确认失效、当前/未知/损坏版本、tombstone、逐层兼容、最终像素 generation、reset 隔离与回滚接缝、取消、提交前/后异常和 28 个边界的 56 次 Crash 注入。中断矩阵在不同平台按实测路径边界数动态枚举。

安全复核中发现并修复：最后依赖核对期间较早已读图片被改动仍可能提交；同一期间新激活同字节换 inode 未被提交前再次检测。新增原地同字节修改并恢复 mtime、替换旧备份/new pin 的负例，未知对象和 pending 保留。Qt 复核同时修复真实双向信号回声、提交后取消提示和关闭/离页后的迟到弹窗。

代表命令（仓库根，解释器需已有 PySide6/numpy/Pillow）：

```sh
PYTHONPATH=app:tools:tests:experiments/qt_quick QT_QPA_PLATFORM=offscreen python -B -m unittest -q test_author_snapshot_history test_author_resource_snapshots test_resource_reset_snapshots
PYTHONPATH=app:tools:tests:experiments/qt_quick QT_QPA_PLATFORM=offscreen python -B -m unittest -q test_client_author_history test_client_author_history_page test_client_author_snapshots test_client_package_manager
```

QML 测试实际加载资源页、点击历史/确认/取消/关闭、读无障碍名称，并控制迟到 worker。资源均为合成 fixture，运行于 Linux offscreen；这不是 Windows 原生、真实安装、游戏或生产图包验收。Windows 工作流应把新三模块加入作者快照组后在最终集成 SHA 上执行。

本次 Linux 定向执行合计 301 次，295 通过、6 跳过。分组为上面核心 79 次（1 Windows-only 跳过）、Qt 24 次全过，另既有作者/事务/升级相邻核心 126 次（4 Windows-only 跳过）、资源导入与恢复 Qt 72 次（1 未提供私有 ZIP 跳过）。不能把这些跳过记为通过。
