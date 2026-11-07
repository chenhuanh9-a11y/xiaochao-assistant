# 安装升级的持久前向恢复

本模块 `app/installer_upgrade_journal.py` 是 stdlib 协调 journal，不执行原生安装、
不读取账户外的信息、不修改注册表/快捷方式，不移动或删除旧程序/资源。
Linux fixture 只能证明文件协议；不代表 Windows Setup、注册表或真实升级验收。

## 调用方前置条件

所有 API 必须在同一物理根的已验证 borrowed Setup lease 内调用，保持 Setup
原独占句柄直到终态。`owner_sid` 必须来自真实当前 Windows token 的 SID，
不得来自可编辑标签或命令行自报身份。模块只验证 SID 格式与持久身份，不自行
建立系统级授权，也不提供 CLI skip 或通用“忽略 pending”开关。

首次资源 prepare、程序备份/替换或 native 变化之前必须先 `begin()` 成功。
普通 runtime 和独立资源 recover 遇 `work/pending-installer-upgrade.json` 均阻断。
仅当前 helper 已验证的 borrowed lease，可在完整 journal 核验后取得进程内、
限定 session/root/原 marker 对象身份及字节的能力，使内部资源 prepare/finish
通过**本次 installer marker**。该能力不忽略其他资源 pending，也不向 CLI 暴露。

## 固定 API 和字段

构造：

```python
InstallerUpgradeJournal(root, *, owner_sid, session_id, target_identity,
                        report_sha256, plan_sha256, restore_watcher=False)
```

- `session_id` 为 canonical UUID4，同时作为 `upgradeId` 和 history 目录名。
- `target_identity` 严格为 `sourceCommit`、`displayVersion`、`numericVersion`、
  `executableSha256`、`resourceInventorySha256` 五字段；不能为空、不能加字段。
- `restore_watcher` 严格为 bool，默认 False，持久字段为 `restoreWatcher`。
  它仅来自本次已验证 Setup 在 Disable 前捕获的原明确开启意图，不根据 watcher
  文件存在或当前 Run 缺失猜测。该字段不是单独的恢复许可；helper 只能在整体
  finalized 的可信短回执返回该意图，Inno 才按既有恢复入口处理。journal 不写注册表、
  不启动程序。恢复实例须显式传回持久的原 bool，不能重新推断或改变。
- `read_status(root, *, owner_sid)` 无 marker 时只读返回 `status=not-needed`。
  有 marker 时完整验证对象和链后返回固定 bindings，供下一 Setup 重建同一实例。
- 实例 `status()` 还可在自己的 marker 已清除后读取 `completed/aborted` 历史。
  非终态历史缺 marker 是错误，不能推断已完成。

状态结果包含 `ownerSid/sessionId/upgradeId/rootIdentity/restoreWatcher/targetIdentity/reportSha256/
planSha256/resourceTransactionId/status/nativeEvidence/nativeEvidenceSha256/generation/
pending/retainedDirectory`。`rootIdentity` 是 `[st_dev, st_ino]`；目录 identity、
链前驱 stamp 和锚定摘要等额外字段是校验证据，不是删除权。

## 顺序与回调

1. `begin()`：发布 `prepared`，此时 `resourceTransactionId=None`。
2. 调用原资源 prepare，然后 `bind_resource(transaction_id, *, verify_resource)`。
3. `start_native(*, verify_resource)`：只允许已绑定的 `prepared`，持久化
   `native-in-progress` 后才返回原生变更许可。该方法不对 `native-in-progress`
   重入放行，防止一次启动的回执丢失后再次执行 native。
4. 当前 Setup 确认原生完成后，用只读观察器取得完整当前证据，调用
   `record_native_complete(evidence, *, verify_native)`，持久化 `native-complete`。
5. 调用 `assert_can_forward(*, verify_native)`，再次核验真实当前程序/native
   状态完全等于完成证据，才能向前提交资源。
6. 资源 commit 且资源 marker 清理完毕后，调用
   `finish_complete(*, verify_native, verify_resource)`，再次核验两边，持久化
   `completed`，最后只删除本事务自己的 installer marker。

`verify_resource(record)` 和 `verify_native(record, evidence)` 都必须返回字面值
`True`。`None/1/False` 均失败关闭。所有回调完成后重读 journal，避免用回调前
快照追加或清理。原生回调必须**重新观测真实当前状态**，不能仅返回旧 JSON、
检查 EXE 标签或信任 Setup 退出码。journal 只负责证据格式和完整性，不代替
Win32 观察器判断注册表、实际卸载器、程序库存和快捷方式。

资源回调必须核对同 root、同 transaction、同 plan、同 target：

- bind/start：核心事务为 `prepared-resources`。
- finish：核心事务为 `committed` 且资源 pending 已安全清除。
- `abort_pre_native(*, verify_resource)`：只允许 `prepared/aborted`，必须证明
  资源尚未开始或已经完整恢复且没有遗留 pending。未绑定 transactionId 不表示
  什么都没发生，可能是资源 prepare 回执丢失；先从核心 pending 找到同 plan
  事务并完成验证。未绑定且没有任何资源 pending 时，可在同一 verified lease
  scope 内，以原固定 target 和本包已验候选重新执行完整资源 inspect；只有所得
  planSha256 与原确认值完全一致才可证明未变更或已恢复旧文件面，单纯 pending
  不存在不够。无法明确判断则保留 installer marker。

`native-in-progress` 中断或缺少完成凭据只阻断，不自动倒退半边。
下一 Setup 只从已验证的 `native-complete/completed` 向前收尾。已进入 native 的
任何状态都拒绝 abort；没有“native失败于是恢复旧资源”的自动分支。

## 持久化与未知对象

目录在 `work/installer-upgrades/<sessionId>/`，不在 map-packages 内。

- `anchor.json`：固定绑定、物理根、work/history/子目录 identity。
- `objects/`：先独占写入并 fsync 的完整私有记录，永久保留。
- `pins/`：已登记 generation 对象的保留 hardlink。
- `generations/<sequence>-<sha256>.json`：不可覆盖 hardlink 发布、连续哈希链。
- `published/`：独立不可覆盖发布凭据，在 start_native 返回前持久化。
  最新 native-in-progress generation 被误删时不能静默退回 prepared。
- `pending-pointer.json`：小型 marker pin。公共 pending hardlink 只保存 schema、
  session、anchor 和初代 generation 的 identity/bytes/hash，约几百字节。

native evidence 为完整非空 JSON dict，最大 16 MiB；record 最大 20 MiB；
深度 64、总节点 500000、单 list/dict 20000、单字符串 4 MiB、dict key 32768
字符（兼容观察器中的完整 Windows 长路径库存，仍受总字节上限）。仅允许
str/int/bool/null/list/dict，拒绝浮点数、NaN、重复键与非 canonical 编码。
证据 SHA256 与不可变 generation 内容及前驱对象 identity 绑定。

未知/损坏 marker、坏 chain、复制到另一物理根、不同 SID/target/plan/report/session、
相同字节换 inode、链接/重解析/特殊对象、丢失已发布记录均不能被认领或覆盖。
发布前中断留下的 UUID 私有对象只保留，不自动采用，也不删除。
发布完成但应答或 flush 失败时以重读结果为准，不猜测一定未执行。

completed/aborted 清标记失败可以幂等重入，仍需当前相应验证。
Windows 删除使用同一 READ|DELETE、share-READ 句柄验证 identity/bytes/hash 并
设置 FileDispositionInfo，只移除该 marker hardlink；保留所有历史证据。
POSIX 是 cooperative lease 下的重验加 unlink，不宣称抵御恶意并发路径替换。
目录 fsync 在 Python 支持的平台使用；不承诺多文件断电原子性。

Windows CPython 3.13 的 pathname stat ctime 和 handle fstat ctime 语义不同；
元数据读前/后及重新打开当前路径均使用 fstat 统一比较，不降低变更检测字段。

## Setup 与 helper 的接线

本实现不把 `ssPostInstall` 异常当成 Windows 卸载登记或快捷方式已回滚的证据。
顺序为：用户确认盘点 → 建立安装协调记录 → 准备资源 → 仅复制程序恢复副本 →
持久化 `native-in-progress` → 退役旧程序文件并交给 Inno 原生安装 →
`ssPostInstall` 保存完整原生完成观察 → 提交资源 → 再次核验原生观察 →
持久化 `completed` 并清协调标记。最后才恢复原来明确开启的游戏启动绑定。

- 完整程序库存由本包的真实安装投影生成，独立 SHA256 固定，包括候选底模、
  helper/runtime、程序、版本报告和所有权清单，不包括用户自定义地图或活动资源。
- 原生观察只读核对同一登录账号/桌面账号、物理安装根、每个程序文件的身份和
  哈希、实际 `unins000..999` 文件、完整卸载登记及精确快捷方式。未知格式和
  权限不足均失败关闭；不调用旧主程序，也不使用快捷方式 Resolve。
- `work/pending-installer-upgrade.json` 独立于资源标记。资源已经提交但整体
  安装尚未核验结束时，正常启动、资源修复与卸载仍被阻止。
- helper 只能借用仍然存活的 Setup 独占资源句柄。临时许可每次重新验证同一
  标记对象及完整协调日志；阶段推进后必须重新开许可，旧 prepared 许可不能
  越过原生边界用于回滚。
- 原生开始前取消，可在验证完整旧资源面恢复后清标记。程序准备阶段不删除、
  覆盖旧程序，因此不需要把旧程序字节盲目复制回去。
- 原生开始后失败或取消，不自动回退程序或资源。只有已经保存完整
  `native-complete` 凭据且现在仍完全匹配时，下次安装器经用户确认可向前
  完成旧目标事务，再开始新目标的盘点。当前安装包版本不能替代旧目标证据。
- `native-in-progress` 中断、完成凭据未写成、文件/登记/快捷方式后来变化时，
  保留全部现场并要求核对修复。此版本没有承诺这些情况能无人值守自动恢复，
  也没有声称已恢复旧 Windows 安装登记。
- 游戏绑定恢复失败不会重新回退已提交安装。若进程恰在协调标记清除后、
  Run 恢复前终止，安装已完成，但绑定可能仍关闭，用户需在设置中核对。

云端 fixture/mock Win32 测试不等于原生 Setup 验收。原文生产安装脚本编译、
实际冻结 helper、唯一测试 AppId 的隔离安装/升级/卸载和故障点检查须分别记录
精确提交；不能把较早版本的测试结论转用于本次新接线。已知 Inno 提取在很长
临时目录路径下可能失败，失败发生在 helper 启动前；不修改系统长路径设置。


## Native evidence admission budget

Before `prepare` calls journal `begin` (and therefore before resource preparation),
and again before `start-native`, the coordinator rereads and SHA-256 verifies the
current Setup program inventory and admits its known native evidence shape using
`preflight_native_evidence`. A failure does not create a journal or prepare resources
on the first boundary. At the second boundary resources may already be prepared;
native program installation has not started and the existing pre-native restoration
path remains required. The stable capacity error is
`native-observation-evidence-budget-exceeded`.

The pure preflight reuses the observer contract validation, canonical UTF-8 JSON,
and journal string/key/container/depth constraints. It charges the exact known
schema, source, contract and digest representation, root ancestor records, every
program record and its repeated drive/segment ancestors, and the report's base64
length. Windows identity/time/attribute integers use their maximum field widths,
physical paths use the volume GUID representation and case folding, and file hashes
and sizes come from the current pinned inventory. Counting is incremental, so a
large inventory cannot cause an enormous repeated-ancestor tree allocation.
There is no new arbitrary file-count cutoff.

Known evidence must fit within 16 MiB minus a 4 MiB native-object reserve and 64 KiB
journal-wrapper reserve, and within 500,000 JSON nodes minus 100,000 native-object
nodes and 4,096 wrapper nodes. The native reserve covers the initially empty users,
uninstall, registry and shortcut observations. These are supported-budget margins,
not a guarantee that arbitrary future registry or shortcut content will fit.
Complete capture, journal bounds and replay verification remain unchanged. The
preflight does not read the old registry or shortcuts. Resuming old native-complete
or completed evidence continues to use its recorded bounded contract, never the
current new package or its admission budget.
