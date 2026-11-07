# 覆盖升级资源重置：核心接口与安装器接缝

## 范围与当前状态

基线：`75d2a750ef3dc657ceb699b7f62a3793936c2f8a`。用户要求覆盖安装时清理旧资源，
包括旧图包，避免不同版本持续混用。实现选择安装过程完成清理，不能把“首次启动
才完成”称为安装已清理。源码、离线验证、Windows 原生安装验收分别记录。

本核心提供盘点、隔离、新底模准备、提交和失败恢复。**仅核心接口通过不代表
Setup 已接线或原生升级已通过**。当前安装包的新 helper、共享锁桥接、确认页面、
Inno 生命周期、打包和 Windows 验收由安装器接缝承担；不能调用旧安装中的程序
执行清理，不能通过无验证的 `--skip-lock` 绕过资源锁。

## 为什么不能扩大现有自动修复的删除权

原 Setup 仅投放新程序和候选底模。`managed_base_resources` 按精确资源合同修复，
不认领改过或未知的文件。这个保护本身正确，但旧文件继续留在活动资源目录，
会持续造成依赖或所有权冲突。旧安装文件列表只列路径，版本号也只是标签，
都不是删除用户后来修改内容的证据。

因此新增一次性的明确确认操作，而不是让日常启动自动删更多内容：

1. 获取选定安装根的资源独占锁，盘点旧资源和旧版本身份文件。
2. 展示范围、大小、图包重下载、修改与未知内容、保留数据及隔离位置。
3. 确认固定的计划 SHA256 后，再次检查完整快照和新候选底模。
4. 先持久化 journal 和新文件对象，再将旧资源逐文件移入同盘隔离区。
5. 发布新底模，保持 pending，状态为 `prepared-resources`，不是安装成功。
6. Setup 真正完成程序替换后验证新资源和隔离对象，提交并清 pending。
7. 安装失败或取消则恢复旧资源对象；不覆盖回滚期间后来出现的未知对象。

## 盘点与数据边界

固定盘点范围：

- `_internal/assets/`，排除整个 `custom-maps/`。
- `_internal/references/`，排除程序构建指纹 `build-fingerprint.txt`。
- `_internal/recognition/`、`_internal/seasons/`、`_internal/qt-author-base/`。
- `_internal/difficult-native-manifest.json`。
- `work/map-packages/` 的图包回执、缓存和历史，排除当前锁及本操作的 pending。
- 上一代 `work/resource-generation.json`，作为资源代次元数据，不是用户路线。

其他位置均不进入隔离选择，尤其是 `assets/custom-maps/`、
`_internal/assets/custom-maps/`、`_internal/qt-editor-base/`、用户路线、设置、
学习与诊断目录。图包目录里已修改的图片或用户自制包可能是用户内容，不能说
清掉后仍有在线原件；预览单列改动/未知数量，原字节和原文件身份留在隔离区。

分类不是额外的自动操作权限：

- `source-reviewed`：与源码收录的历史底模或作者图库精确路径、大小、哈希一致。
- `receipt-matched`：匹配本地回执声明。回执不是发行方签名。
- `modified-declared-resource`：曾被声明的路径现有字节不同，保全改动。
- `unclassified-resource`：不能证明来源的资源位置文件。
- `package-state-or-history`：包回执或历史元数据，可能已损坏。

确认覆盖上述固定范围后，四类资源字节都可移入隔离，**不递归删除任何目录**，
不删除隔离内容。这样未知旧资源不会继续进入活动资源读取。未确认、快照变化、
链接/重解析点、异常文件类型、目录占用新文件路径、普通文件占用祖先路径、
枚举/读取失败、资源量上限、空间不足均在能确认的边界失败关闭。
当前不自动修复目录/文件结构碰撞；不能宣称所有损坏安装都能无条件重置。

旧图包/底模升级、导入、删除 pending 或尚未处理的 helper handoff 必须先处理，
不能把正在进行的事务当旧垃圾一起隔离。损坏 pending 也不能猜测已完成。

## 安装器和 helper 合同

核心入口位于 `app/resource_upgrade_reset.py`：

- `inspect_resource_upgrade(root, candidate_directory=..., target_identity=...)`：返回完整预览和
  `planSha256`，不修改活动资源。
- `prepare_resource_upgrade(root, expected_plan_sha256=..., confirmed=True,
  candidate_directory=..., target_identity=...)`：重验预览，返回 `transactionId`、`prepared-resources`、
  `targetBundleId`、`retainedDirectory`、`packagesToDownload`。
- `finish_resource_upgrade(root, transactionId, setup_succeeded=True)`：只在
  当前 Setup 明确完成后调用。默认或 `False` 为回滚。
- `recover_resource_upgrade(root)`：未提交恢复，已提交仅完成 pending 清理；
  不回退已经提交的新资源。
- `resource_upgrade_status(root, transactionId)`：只读核对 journal 后返回提交阶段。
  未知回执结果必须先查询，不能猜测进程报错就是未提交。只有确认资源未提交才可
  先回滚程序再恢复资源；已提交按已提交处理，无法判断时两者都禁止擅自回退。

可选 `target_identity` 严格限定为 `sourceCommit`、`displayVersion`、
`numericVersion`、`executableSha256`、`resourceInventorySha256` 五个字段，纳入
预览哈希、journal 和 generation。安装器需提供本次包的固定值；缺失表示未知，
不能用观察到的旧标签填充。字段仅供溯源，不扩大作用域或赋予删除权限。

`candidate_directory` 是本次安装包提供的 `manifest.json` 与
`payload/<资源相对路径>` 目录，可在临时解包区。每个候选文件都验证大小和哈希，
S1 仍必须符合源码审过的完整资源合同。不能用旧安装的候选目录冒充新包输入。

安装器在程序文件被覆盖**之前**盘点并准备，否则旧 `version.json` 等身份文件
已经被新版本覆盖，无法准确追踪旧资源来源。helper 会话回执还须绑定当前 Setup
升级 ID、固定源码 commit、显示/数值版本、目标 EXE 哈希与核心 transactionId、
planSha256。目标程序身份来自当前包，而不是旧安装的可编辑标签。

核心内部复用短期 `resource_read_lock` / `resource_write_lock`。Setup 必须在
整个安装、失败回滚直至终态期间保留既有 share=0 独占锁。新 helper 借用同一
内核锁对象时，必须验证 Setup 父进程/句柄、物理安装根、最终锁路径、文件类型、
非链接及独占状态后注册自己的 runtime lease。两个程序不得分别争同一把锁，
不得在操作之间释放主锁。不同安装目录仅处理被确认的那个物理安装根；路径
别名不能扩大目标，复制的相同文件集也不能复用另一目录的计划。

父进程异常退出不等同于成功。没有明确 commit 的 journal 保持 pending，后续
安装需先恢复，不能盲目继续。新版正常读取和自动底模修复均被此 pending 阻断。
旧程序不认识新 marker，因此同一 OS 独占锁跨整个安装期是必要条件。

## 事务和恢复证据

隔离位置：`work/resource-upgrades/<transactionId>/`。

- `journal.json`：原计划、物理根身份、旧版本观察、所有旧/新文件路径、字节数、
  哈希及原文件身份、目标资源清单和阶段。
- `old/<index>`：按原计划排序的原对象，不是重编码副本。
- `new/<index>`：发布前固定的新对象，发布使用排他 hardlink，不能覆盖后来文件。

阶段为 `preparing → moving → prepared-resources → committed`，未提交失败恢复为
`rolled-back`。回滚前整批检查，再逐件复查；旧文件在原位或隔离中的身份必须
一致，新文件只有与已登记对象身份、大小、哈希同时匹配才可撤回。任何未知
对象阻止回滚且保留 pending 和两份证据，不强制覆盖。

Windows 的大小写不敏感边界应用于保护根、候选路径和旧/新路径配对，不能以
`Custom-Maps` 等拼写绕过保护。pending 先写入私有完整对象，再以 hardlink 原子
发布；清除时同时核对预期字节摘要和保留指针的文件身份，不能采用后来替换的
陌生 marker。

重复终态通知不会再次执行移动/删除。`committed` 已持久化但 flush/清 marker
失败时，恢复只处理 marker，不能重新恢复旧资源。恢复后的新暂存对象及已提交
隔离保留在活动目录之外；当前没有自动清空隔离、自动过期、或把旧资源恢复到
新版本活动目录的入口。磁盘空间预检不等于配额/断电保证，实际 I/O 仍可失败。

这些是合作进程锁、对象身份及路径验证保障，不是文件系统断电原子性，也不声称
能消除任意外部写者在每个校验与系统调用之间的所有竞态。

## 历史版本覆盖证据与未知

- 已发布 1.1.1 源码边界：`161e14f7d50b4c7ec27e2d29b67ecc060912da9c`，
  version.json 为 `1.1.1 / 1.0.0.23`。
- 仓库 1.1.0 元数据可见于 `2289801`，为 `1.1.0 / 1.0.0.22`；不能仅凭这个
  commit 将某个本机安装认定为该次最终发布。最终原包身份仍须独立核对。
- 历史 S0 1.0.2 有源码审过的 307 文件精确库存，S1 有原完整三模式及退役投影
  库存，旧作者图有独立哈希目录；这些只能证明匹配文件，不证明整个安装没改过。
- 核心按实际盘点而非版本白名单执行，因此缺版本标签、混合标签、残缺/坏回执
  仍可进入明确确认的隔离计划，不能静默认领或自动删除。

离线测试覆盖 1.1.0、1.1.1、可辨识历史标签、改动/未知图包、不同物理目录、
取消、部分移动、发布中断、后来文件、损坏隔离、身份/计划篡改、空间不足、
重复终态和不确定提交。它们是合成文件合同，不是这些原 Windows Setup 的实际
覆盖安装验收。最终必须在原生 Windows 验证同一新包从 1.1.0、1.1.1、历史安装
升级，以及换目录拒绝/选择、锁别名、取消、进程退出和失败恢复。

## Windows 安装器接线

`tools/qt-resource-reset.iss` 使用当前 Setup 中的 `star-map-reset-helper.exe` 和
独立 `_star_map_reset_runtime/`，通过 Inno `ExtractTemporaryFiles` 临时提取，
不运行旧安装里的 helper 或主程序。每次调用前核对完整 helper/runtime 文件库存、
当前目标 release-report 和候选 manifest 的编译期固定 SHA256。打包和 installer-only
重封装都要求新 helper 完整库存；旧构建缺失此 helper 必须 full freeze。

Setup 在最终安装根持有原有 share=0 句柄。`installer_resource_lease.py` 只允许
活直接父进程，核对 PID、创建时间、实际映像路径及哈希后 DuplicateHandle，不关闭
父句柄；验证磁盘文件、单链接、非重解析点、规范化卷 GUID 路径，以及读/写探针均
得到真正的共享冲突，再登记本进程 runtime lease。没有跳锁参数、先放锁再争锁或
结束进程的分支。每个调用结束只关闭 duplicate，Setup 原句柄直至回滚结束才释放。

`PrepareToInstall` 在锁内做只读盘点和默认“否”的明确确认；静默覆盖升级拒绝隐式
同意。`ssInstall` 在程序备份/替换前按同一 plan SHA256 准备资源；`ssPostInstall`
除原生文件安装完成外，还核对当前安装 EXE 和 release-report 的目标哈希，才提交
资源。失败退出先恢复程序文件，完整恢复后再恢复资源；程序回滚不完整则保留
pending/隔离并不恢复游戏启动绑定。helper 回执放 Setup 私有临时目录，不放图包区。

Inno 在 `ssPostInstall` 捕获脚本异常后仍可能进入普通完成流程，不能依靠抛异常
触发原生回滚。所有资源未 finalized 的状态均改写完成页并提前设退出码 64；
退出码查询发生在 `DeinitializeSetup` 前，最后重试成功仍保守保留非零。
若原生文件安装已完成、资源却未提交，现有精确文件回滚不包含已写的 Windows
安装登记和快捷方式。会尝试恢复程序/资源文件，但明确报告仍需原安装包修复登记，
保留备份并关闭游戏启动绑定，**不宣称整个安装完整回滚**。没有快照/逐项核验的
用户快捷方式和注册表不由这一步猜测覆盖。这仍是原生失败验收需要覆盖的恢复边界。

该未知状态从 `ssInstall` 完成自有备份、交还 Inno 前就开始，而不是等到
`ssPostInstall` 失败才设置。Inno 可能先删除并重建卸载登记，再保存卸载日志；
中间失败的原生卸载回滚不能恢复旧登记。故即使没有到达 `ssPostInstall`，
也不能只凭程序/资源文件恢复就自动恢复 Run。失败时明确说明登记/快捷方式恢复
尚未确认，保留备份、保持绑定关闭；这项保守收紧不是完整登记补偿。

提交结果不确定时先读取 durable journal。必须区分两个状态：

- `committed` 代表程序/资源不能倒回旧版本，禁止只回滚程序。
- `finalized` 还要求幂等提交回执确认 pending 已安全清理；否则完成页和错误提示
  明确说明暂不可启动，并保持游戏启动绑定关闭，不宣称整体可用。

下一次交互 Setup 可对已 committed 的遗留标记再次征求确认，核对原 journal 的
目标身份与当前 installed EXE/release-report 后仅完成清标记，不恢复旧资源或删除
隔离区。未提交中断仍阻断新升级，必须同时核对恢复程序和资源，不能盲目恢复半边。
卸载既在进入时早提示，也在获得资源锁后重查 pending，避免用户停留向导期间
另一升级留下待恢复状态后被卸载移动或清除。

新增 `tests/test_resource_reset_installer.py` 使用合成文件验证协议、版本身份、回执
丢失、已提交但清理失败、下一安装恢复边界、构建库存及事件顺序。单独 Windows-only
用例通过真实父子进程验证 DuplicateHandle 和 share=0，不可把 Linux skip 写成通过。
源码/协议测试不代替正式脚本 Inno 编译、冻结 helper 或真实隔离 Setup 验收。

## 作者快照接缝

基于已集成作者快照及 Windows metadata 修复的 `e5defc7f4bf5bc1c66ae5ac90d13991359c54178`
接入资源重置。`pending-author-snapshot.json` 与 `pending-resource-reset.json` 的阻断
保留并集，不改变 Setup 持锁、父子句柄核验或程序/资源提交顺序。

盘点按扫描到的 `author-snapshots/active/` 记录，复用 `SnapshotPackage` 严格核验
activation、固定 manifest、receipt 和当前 generation，将仍启用的作者 ID 加入
`packagesToDownload`。此读取不解码图片、不推定当前结构兼容性，也不添加文件所有权；
已损坏的图像仍可以提示重新导入，但全部旧快照和未知字节继续按原清单隔离保存。
停用 tombstone、损坏或无法核验的 activation/manifest（包括同名空目录）不作为启用身份，也不会借
遗留平面回执复活提示。确认对话显示已识别图包数与最多 12 个 ID，完整提示清单仍在
plan/journal；没有已识别 ID 不等于没有旧文件。

`test_resource_reset_snapshots` 的 14 项合成真实事务覆盖正常启用、多版本历史、
tombstone/坏元数据与遗留回执、损坏图像、只读提示、确认后激活变化、两个 pending
方向、prepare 后意外作者 pending 阻断 commit，以及 prepare/rollback/commit 的
快照身份和个人路线保留。联合 13 组源码回归共 232 项，228 通过、4 项平台测试跳过。
这不补齐前述原生安装登记/快捷方式回滚缺口，也不构成 Windows Setup 验收。

2026-11-07 的原生 Inno 6.7.3 首次编译发现：Pascal 续行若以 `#13#10`
开头，会被 ISPP 作为预处理指令，停止于 `qt-resource-reset.iss:185`（exit 2）。
统一将三个安装/卸载脚本共六处字符常量移回上一行，并增加源码回归。
该次只使用合成 stage 和 `/O-`，没有生成或运行 Setup；修正后仍需同提交重编译，
不能以这项源码检查代替 Pascal 编译或安装验收。

后续 `c3b912e` 原文进入 Pascal 后，发现 `[Code]` 内引用的 watcher include
前三行 `;` 注释非法。只在一次性副本把这三行改为 `//` 的原生 `/O-` 探针通过
（Inno 6.7.3，exit 0）；云端同步修正该注释并增加 include 语法回归。这只证明
该副本的 Pascal 可编译，不能写成 `c3b912e` 原文通过，也未执行任何安装。

参考 [Inno 临时提取目录规则](https://jrsoftware.org/ishelp/topic_isxfunc_extracttemporaryfiles.htm)
和 [Windows DuplicateHandle](https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-duplicatehandle)。
