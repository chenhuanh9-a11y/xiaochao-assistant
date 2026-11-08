# QtIFW Windows 隔离原型与迁移方案

2026-11-08，本地接续云端提交 `9c1ca3a80655d67fe05c6b31416600d025023069`。
保留二楼组合修复 `142001c884029a8fb9dc08d9760e5149b9cb72da`，未修改正式 Inno 安装链、运行时或资源锁，未对现有星图执行安装、卸载、清理、进程操作或发布。

本轮产物是合成原型和迁移方案。QtIFW 的正常组件生命周期可用，但更新失败和首次安装硬中断没有自动恢复能力，不能据此直接替换现有安装器。

## 工具与复现

Windows 官方 QtIFW **4.11.0 / Qt 6.9.3**。从[云端交接](qtifw-prototype-handoff.md)所列官方 Windows URL 下载，实际大小为 56,804,453 bytes，实际 SHA256 为 `c47201c4f6a82a8b607daa245237f40831d78425e904edd1514b71fd17efefc1`，匹配交接值后提取并执行 `installerbase.exe --version`。
本机先前取得的 4.7 工具没有用于本轮验证。Inno 合成夹具由本机 Inno Setup 6.7.3 编译。

```powershell
python -m unittest discover -s tests -p 'test_qtifw*probe.py' -v
python tools/probe_qtifw_windows.py --ifw-bin <QtIFW-bin> --inno-compiler <ISCC.exe> --output <全新测试根>
```

`--output` 必须不存在。输出下的 `targets` 才是可操作目标，程序拒绝外部路径、路径穿越与 reparse point。夹具 AppId 随机且与生产身份不同，只查询其自身 HKCU 卸载键并执行其自身卸载器；不枚举或运行生产卸载器，不创建产品快捷方式，不启动星图。

Windows 脚本复用云端 `prepare_packages`，保留组件及 UNDO 行为。Windows 原生工具日志可能为 ANSI；原始字节完整保留，只对读取副本选择 UTF-8/Windows 代码页。没有复制自建整树备份/回滚引擎。

## 本机证据与结果

完整原始日志只保留在本机忽略目录；未推送个人路径、日志或二进制。下表以合成文件和独立检查为准，不能外推为真实星图安装或游戏验收。

| 路径 | Windows 实际结果 | 证据边界 |
| --- | --- | --- |
| 新装 v1 → 维护器更新 v2 | 成功；v1 独有文件移除；资料保留 | 对组件实际文件逐字节比对，并读取维护器 `list` 核组件及版本 |
| v3 更新 Execute 返回 47 | 安装器退出 3；旧程序未恢复，维护器仍存在 | 原始失败状态完整保留，不称自动回滚 |
| 维护器从已知 v2 仓库执行 `install` | 成功；恢复 v2 | 恢复终态再次逐字节核程序，`list` 核版本；只证明此故障的前向修复 |
| `purge` 默认卸载 | 组件程序移除；work 与未知文件保留 | `RemoveTargetDir=false`，资料在安装后创建，不属于组件 |
| 明确选择清理合成资料 | 卸载成功后，仅已知两项测试资料移除；未知文件保留 | Python 夹具适配器，尚无产品 GUI 清理页面或产品资料删除能力 |
| 真实 Inno 夹具 → 同目录 QtIFW | 保留与清理两种测试选择均通过 | 查询真实注册表身份/目标；旧卸载完成后才装 Qt；核最终 payload 及 `list` |
| Inno → 新目录 QtIFW | 成功；已知夹具资料复制到新目录，旧副本保留 | 仅测试“新目录 + 保留”；未自动搬移或清空旧目录 |
| Inno 已卸载后 Qt 故意失败 | 退出 3，资料保留；用已知旧测试 Setup 重装恢复旧程序 | 显式前向修复，不是 QtIFW 撤销了 Inno 卸载 |
| 首装提取后停止本次安装器 PID | payload 已存在，无维护器；非空目录重试退出 1 | 只停止自行创建的进程；Execute 子进程 12 秒自行结束；不是断电测试 |
| Windows 向导 Welcome → Quit | 窗口退出；目标目录未创建 | 通过 computer-use 读取真实窗口并点击 Quit；只验证安装前取消 |
| 路径/资料反例 | 14 项源码测试通过，其中 7 项为 Windows 接续测试 | 包括真实 junction、hardlink、外部哨兵、未知/被改资料、失败后禁止清理及 ANSI 原始日志 |

原始证据目录（相对于本机 checkout）：

- `work/qtifw-windows-20261108-r1`：原生日志为 ANSI 导致观察脚本解码失败；实际新装成功。未删除这次现场。
- `work/qtifw-windows-20261108-r2/windows-result.json`：生命周期及清理测试完成；首次迁移在旧卸载器自删除尚未完成时，被 legacy guard 拒绝，整轮没有标为完成。
- `work/qtifw-windows-20261108-r3/windows-result.json`：修正等待旧测试卸载器自删除后，迁移与硬中断观察完成。显式 `--skip-lifecycle` 复用 r2 生命周期证据，没有将它记作 r3 重跑通过。
- r3 的第一次中断后重试与 GUI 观察短暂重叠，存在 QtIFW cache lock 警告。关闭 GUI 后单独重试，`interrupt-retry-serialized.log` 无该警告，仍因非空目录退出 1。自动恢复的结论依据串行重试。
- GUI 验证目标为 r3 的 `targets/gui-cancel`；退出后再次枚举确认窗口消失，并确认目标目录不存在。

`probeCompleted` 仅指所选观察流程完成。预期失败仍记录真实非零退出码，不变成产品通过，也未跳过失败样本。

## 真实 payload 接缝复核

对现有 `142001C8` 冻结目录执行既有 `validate_managed_runtime_reuse` 与 `write_installer_program_inventory`，使用原 build-record 的源码身份，而非将旧 EXE 改称本分支构建。
结果在 `work/qtifw-payload-preflight-20261108/result.json`：

- 原 helper、watcher、EXE、report、profile、candidate manifest 验证完成。
- 3,648 项程序安装清单重新生成，与原 `reset-program-inventory.json` 字节完全一致；其中 698 项 candidate 文件、158 项只读 editor-base 文件。
- 没有 work 或活动地图/editor 文件。`_internal/references/build-fingerprint.txt` 按现有 `_PROGRAM_IDENTITY` 属于程序身份，须保留。本轮额外的粗略前缀断言曾误将其判为活动资源；核对源分类后纠正了该断言，没有改动清单、分类器或原始构建。

这里只复核了输入接缝。真实 payload QtIFW 适配器、打包、安装、启动和资源初始化都尚未验证。

## 正式迁移的建议执行链

1. 复用真实构建清单及程序/只读 candidate 分层，不整棵复制当前安装目录。组件不能拥有 work、活动资源或用户文件；旧 `_installer-owned-files.txt` 不能自动授予 QtIFW 删除权限。
2. 沿用现有路径/SID/进程身份、watcher、资源 lease 和 pending 恢复检查。核旧 Inno 注册表记录、实际路径和身份，明确展示同目录/新目录及资料保留选择；在旧卸载前核目标碰撞和已知可重装来源。
3. 执行旧卸载后等待其实际完成；退出码为零不足以证明自删除已完成。旧卸载失败就停止，不继续 Extract，也不手工删除残留来绕过 legacy guard。
4. 安装与逐字节核程序，确认维护器组件/版本，再由当前运行时合同初始化资源。失败状态必须明确暴露；本轮已证明已知仓库的维护器重装和已知旧 Setup 重装两种特定前向修复。
5. 保留资料为默认。清理必须是独立明确选择且有正式资料范围、未知文件保留和失败恢复合同；本轮两项夹具文件的清理不是该合同。现有 reset helper 不等于完整卸载清理接口。

## 正式替换前仍需完成

- 真实程序 inventory 到 QtIFW 的适配器、版本/身份、许可及适用源码提供说明、快捷方式和 helper/watcher 生命周期。
- 面向用户的迁移和保留/清理页面，以及真实资料清理实现；跨目录真实资料迁移的恢复。
- 安装中/更新中用户取消、进程占用、不同 SID/权限、首次硬中断现场的受控恢复、断电重启。安装前 Quit 不能证明这些路径。
- 真实 payload 安装、首次启动、现有资源和导入资料保护。原 `142001C8` Setup 保留为当前候选，不替换默认安装链。

已评估社区 `qt-installer-framework-config` 技能目录项，但原始文件来源未核实且不覆盖 Windows 迁移/恢复，因此没有安装；使用官方文档与已有 computer-use 技能。

官方参考：[命令行](https://doc.qt.io/qtinstallerframework/ifw-cli.html)、[操作与撤销](https://doc.qt.io/qtinstallerframework/operations.html)、[配置](https://doc.qt.io/qtinstallerframework/ifw-globalconfig.html)。
