# 同包资源升级与回滚

地图包仍使用 `map-package.json` schema/revision 1，客户端版本及全部 `requiresFiles` 的大小、SHA256 和资源路径白名单继续严格校验。地图包不能更新代码、基础目录清单或任意用户文件。

## 所有权与替换条件

导入器只根据安装目录 `work/map-packages/<packageId>.json` 中已有的 receipt 确认旧包所有权，不接受新 ZIP 自称的旧文件哈希。当前文件必须同时匹配旧 receipt 的路径、大小和哈希，且不能由另一包的 receipt 声明所有权，才允许同包替换。

未知文件、被用户修改的文件、坏 receipt、链接路径以及不兼容基础资源都会继续拒绝。没有 receipt 的旧内置包不能靠扫描目录补造所有权。构建维护者可以从经过哈希核实的原始包清单，在独立构建 stage 中验证全部文件后生成 receipt；这不能用来绕过真实用户目录的冲突。

相同包重复导入且资源清单等价时，保留原 receipt 和事务身份，不破坏上一次升级的回滚能力。新包不再列出的旧文件不会自动删除。

运行中的客户端只允许在空闲资源租约内更新白名单作者图片与精确 sidecar，并先取得所属窗口的更新确认；复用下述备份/receipt-last 事务，成功后失效旧预览代次并重载资源上下文，无需重启。核心数据变更、未完成事务恢复及 CLI 维护仍要求关闭应用。活跃对局先结束，文件被其他任务占用时回滚并稍后重试，不强制解锁。取消在 receipt 提交前恢复旧包；提交后到达的取消不把成功误报为已回滚。取消/失败的已登记事务对象可能保留用于检查与恢复，不承诺立即释放磁盘。

## 命令

先退出应用。Python 命令用于维护与离线验证；打包客户端仍需重新构建其导入 helper 才能获得新行为。

```powershell
python app/star_map_import.py inspect ./private-input/map-package.zip ./isolated-install
python app/star_map_import.py install ./private-input/map-package.zip ./isolated-install --application-closed
python app/star_map_import.py recover - ./isolated-install --application-closed
python app/star_map_import.py rollback <upgradeTransactionId> ./isolated-install --application-closed
```

`inspect` 只读。`install`、`recover`、`rollback` 需要关闭应用并取得同一个排他导入锁。应用正在运行或目录不可写时拒绝，不应提权绕过。CLI 中 `recover` 的 `-` 只是保留位置参数，不读取该文件。

## 事务与用户文件保护

升级先完整验证 ZIP，再在 `work/map-packages/upgrades/<id>/` 保存新对象、旧 receipt、旧资源备份和持久 journal，使用 `pending-upgrade.json` 标记未完成事务。Windows 通过同一个受保护的文件 handle 校验旧文件后执行不覆盖 rename；新文件通过排他硬链接发布。

普通异常尝试自动回滚；进程中断后，新的检查/导入会要求先执行 `recover`。主动回滚会先检查所有资源、receipt、备份和对象身份，存在已知冲突就不修改任何资源。检查后出现并发变化时仍保留逐项身份/哈希保护；不能安全恢复时保留备份和 pending 标记并报告冲突，不能强行覆盖用户文件。此时应保持应用关闭，先备份并处理报告中的冲突，再恢复。

成功后也保留备份以支持按事务 ID 回滚。多个升级按相反顺序回滚；不要自行改写事务目录或 receipt。保留旧资源会占用额外磁盘，下载 ZIP 缩小不等于升级后立即释放旧资源空间。新构建的资源输入不包含这些 `work/` 备份。

文件内容与 journal 已 flush/fsync，支持进程中断恢复。Windows 目录元数据没有可移植 flush，不承诺断电或故障磁盘下的原子性。POSIX 的路径操作并发保证弱于 Windows；当前本机临时文件验证覆盖的是 Windows。

## Q90 构建资源契约

[resources-q90.lock.json](resources-q90.lock.json) 固定已认可的四包 ZIP、默认两包策略、基础清单 overlay 及资源文件清单的哈希。困难 WebP 使用原尺寸 Q90 和精确 alpha；PNG 为无损优化；结构与识别 NPZ 原字节保留。`.lossless.webp` 是历史兼容文件名，不能据此声称新内容为无损。

基础清单 `_internal/references/nightmare-author-route-catalog.json` 由程序构建管理；Q90 包依赖其新哈希。需要在**独立、已知来源的构建 stage** 中应用匹配的 base overlay，再使用优化包。地图导入器不自行覆盖该基础文件。旧 0581 客户端不能仅靠新 ZIP 原地获得此次升级逻辑；下一次仓库构建应包含新源码/helper 和匹配资源。

默认资源 stage 只选 `difficult-zhan10` 与 `nightmare-suju`。两个可选作者包仍是外部资源，不改变默认策略。将清洁的 `default-resources/_internal/` 合入新构建的资源输出，排除验证目录、旧包、事务备份与版本文件；显示/数字版本由该次仓库源码确定。核实锁定清单后记录实际源码 commit、资源清单 SHA256 和最终产物 SHA256。

已完成的本机隔离验证：旧冻结资源到 Q90 的 95 个困难文件和 72 个苏橘文件升级，升级后全包检查，再回滚并恢复基础清单；465 个资源文件全部回到原冻结哈希，265 份 NPZ 始终字节不变。另有 fixture 覆盖未知文件、用户修改、普通失败、中断恢复、重复导入和回滚冲突预检。本次未构建新 PE/安装包，未改运行安装，未进行 GUI/游戏测试。此前候选的解码/坐标验收属于交接证据，不扩大为本次游戏实测。

作者图像只保存在外部候选与本地构建输入，不随本次源码提交公开；原署名和来源说明应随资源保留。本项目维护脚本不会给第三方图片新增开源许可证。
