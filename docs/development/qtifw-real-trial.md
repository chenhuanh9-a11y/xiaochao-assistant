# PR #110 合并前审查（最新）

本轮只推进安装器审查及源码修复，不重新开发试用原型，不打正式包，不修改地图导入 PR #111 的工作树。

审查确认并修复三项问题：卸载构造时的短锁允许晚到运行者进入；pending 资源事务会阻止故障安装被彻底卸载；安装完成信号内的校验异常不能可靠改变原生成功状态。修复复用现有 Windows 父进程/物理锁对象校验：支持 helper 改为 one-folder，在原生删除前将独占资源句柄交给实际 QtIFW maintenance 父进程，一直留到它退出；不另造备份或回滚引擎。彻底卸载不要求资源先恢复可运行，UNDO 只记录 `scheduled`，退出后目标实际消失才算清理完成。安装字节校验加入原生 DO 操作，完成页读校验结果；完成信号处失败也转换为非成功状态。归档复用比对整个程序清单，避免同源码/同文件数掩盖冻结字节差异。

30 项定向源码/真实文件系统测试通过。两文件原生 QtIFW 回归保留在 `tests/windows/qtifw_native_lifecycle.py`，只重定向测试快捷方式目录，不运行合成 EXE，不替换准备、提取、校验或卸载算法：

```powershell
python -u tests/windows/qtifw_native_lifecycle.py --ifw-bin <QtIFW-bin> --output work/<全新证据目录>
# 单独复现修前缺陷（不是旧代码通过验收）：
python -u tests/windows/qtifw_native_lifecycle.py --ifw-bin <QtIFW-bin> --output work/<另一个全新目录> --baseline-ref 564c35b
```

初轮原生前后对照在本机 `work/qtifw-pr110-native-audit-20261108/before2` 与 `after2`：坏 archive 和完成信号删文件时，修前错误返回 0，修后均为 3；修前晚到租约可进入并发生程序删除，修后租约被阻止，最后目录确实移除；pending 卸载修前退出 1，修后退出 0 且目录消失，外部哨兵保持；取消后程序保留、租约恢复可用。所有输入均为两个专用合成文件，未启动真实星图或操作用户安装。独立最终审查未发现新增 P1/P2 阻碍。

边界：原生 Windows VBS 在 maintenance 退出后才删目录；父进程退出释放锁至 VBS 真正删完目录之间，不宣称仍受独占锁覆盖。需在原生进程退出后核对清理终态。历史 r16 Setup 没有本轮源码修复，不能当作修后正式包；正式包继续等待地图导入统一及联合验证。

合入 `main` 的实际依赖：PR #110 当前 base 是 PR #109 的 `142001c`，并非 `main`。当前 `origin/main=029bf1c` 是祖先，但 `142001c` 相对它已有 588 个文件的前置开发差异；直接把 PR #110 改目标会一并带入这些变更。仅移植 18 个 QtIFW 新增文件也不能运行：`main` 缺少 `resource_transaction.rename_no_replace`、`build_qt_client.write_installer_program_inventory` 等所需接口，实际导入已复现 ImportError。不得以“没有文本冲突”代替依赖链审查，不擅自接管其他任务的集成。

---

# r16 独立新装与彻底卸载试用记录（审查修复前）

2026-11-08，用户已明确调整为：独立新包，旧版由用户卸载；安装和卸载去掉组件、维护动作等多余选择；卸载使用单一“彻底卸载”入口。下方旧候选记录只保留为历史，其默认保留资料策略不再适用于当前包。

当前 Setup：`work/qtifw-real-trial-20261108-r16-simple/Xingtu-QtIFW-Local-Trial-Setup.exe`。

| 字段 | 值 |
| --- | --- |
| 安装器来源 | `bb1a1d58183a5f868666fb0f58178c99578819cd`，干净远端提交；撤销后续导航试改后当前安装器源码与之完全一致 |
| 程序来源 | 原冻结 `142001c884029a8fb9dc08d9760e5149b9cb72da`，3,648 项程序文件 |
| 程序版本 | 1.1.2 / 1.0.0.24，指纹 `142001C8`；QtIFW 组件 1.0.0 不是新产品版本 |
| 字节数 | 322,578,883 |
| SHA256 | `62a96b06d9f8674b9f28efc11c9b774ec4a361fad209e51a83c1c69c6f085729` |

## 当前交互与边界

- **安装**：中文目录页预填 `%LOCALAPPDATA%/Programs/星图试用版`；“浏览”选择父目录后自动添加“星图试用版”子目录，也可编辑完整路径。下一步确认位置，再点击“安装”；不显示组件、许可、开始菜单目录选择或侧栏步骤。不自动启动程序。
- **打开**：桌面“星图（试用版）”及开始菜单“星图试用版/星图（试用版）”直接指向 `<安装位置>/program/星图.exe`，工作目录为 `program`。完成页明确说明这些入口。
- **卸载**：开始菜单“星图试用版/卸载星图试用版”或系统卸载项，直接进入“彻底卸载星图”。一个“彻底卸载”按钮移除安装目录及其中的程序、地图、设置、缓存和日志，移除桌面/开始菜单入口、开始菜单目录和卸载注册项；安装目录外的文件保留。

新安装只接受新建或空的本机目录；已有文件、已有完整安装或其他候选的归属标记会被拒绝，不自动卸载旧版，不迁移旧资料。浏览及目录校验为只读，取消不创建目标。提取前重新检查目录；归属绑定本次身份、原程序来源和完整目标路径，复制记录不能接管另一目录。根、相对、构建、系统/网络路径及链接被拒绝；卸载前检查资源租约和目标内对象。

同次首次安装中断后可按自身归属重试，将原清单内残留保留到 `recovery-files`；这不是覆盖一个完整旧版。新策略由原生 QtIFW `RemoveTargetDir=true` 和标准快捷方式/UNDO 生命周期执行彻底卸载，支持工具回执另存本地试验支持范围。

这是本机试用候选，支持工具仍绑定本机构建范围，未正式分发。普通程序的生产单实例身份不变，未验证并行运行。未自动启动普通程序或游戏；本轮不重新宣称游戏验收。原 PR #109 / `142001C8` 和用户当前安装未被本轮接管。

## 本轮证据

- 26 项定向测试通过：`python -m unittest discover -s tests -p 'test_qtifw*.py' -q`。新增真实文件系统覆盖新目录只读校验、校验后文件到达、归属不能移植、现有安装内路径及根/相对/构建目录拒绝。
- `work/qtifw-directory-before-after.json`：同一显式新目录输入，旧入口拒绝 `outside-trial-targets`，新入口只读接受后可实际准备目录。
- r14 实际打开原生文件夹选择器，选择普通文件夹后自动追加子目录并进入确认页；取消后目标不存在。后续导航修订不改浏览/校验代码。
- r16 原生新装退出 0，3,648 项程序字节核对通过；COM 只读核对三个快捷方式的目标及工作目录。原生 `purge` 退出 0 后，安装目录、快捷方式、开始菜单目录和卸载项均消失，目录外哨兵字节保持；只使用专用合成资料。
- 最终包独立记录在 `work/qtifw-real-trial-20261108-r16-simple/flow-validation/native-result.json`，记录安装与卸载退出码 0、3,648 项程序完整、快捷方式及其工作目录正确、安装目录/快捷方式/卸载项移除和外部文件保留。实际中文单页卸载的截图和控件树也保留在该目录。
- 最终包另走原生 GUI 自动旅程：直接读取包内原控制脚本，仅替代目录输入和导航点击，不替换准备、校验、提取或卸载逻辑。`flow-validation/gui-result.json` 记录新装、卸载均退出 0，3,648 文件/三个入口核对通过，安装目录、快捷方式、开始菜单目录和卸载项均移除，外部文件保留。日志的 `FLOW_NATIVE_GUI: true` 证明没有用 CLI 的更早准入路径代替 GUI；verbose 展开详情后的进度页另补一次继续操作。普通程序及游戏没有启动。

本轮保留失败：r13 隐藏 introduction 跳过元数据加载，未作为成功安装；r14 CLI 对自身归属标记触发的非空确认按拒绝策略退出；r15 因 Virtual 与 Default 互斥而打包失败。r17 额外导航试改在 GUI 构造中连接不可用信号，导致启动拒绝；已撤销该试改，交付实际验证完成的 r16。所有失败候选/现场保留，没有放宽通过条件。

## 公告文案（待正式发布）

> 本次改为独立安装包，请先下载新版安装包，备份需要保留的地图、路线和设置，卸载旧版后再安装新版。
>
> 新版可选择安装位置，安装后可从桌面或开始菜单打开。“彻底卸载”会清除安装目录及其中的数据；安装目录外的文件不受影响。

本轮仅准备文案，未发布生产公告或正式安装包。官方依据：[自定义页面](https://doc.qt.io/qtinstallerframework/ifw-customizing-installers.html)、[原生目录浏览](https://doc.qt.io/qtinstallerframework/scripting-qfiledialog.html)、[页面与按钮生命周期](https://doc.qt.io/qtinstallerframework/scripting-gui.html)、[标准操作与 UNDO](https://doc.qt.io/qtinstallerframework/operations.html)。

---

# 以下为 r6-r12 历史证据，非当前交付策略

2026-11-08。接续 [PR #110](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/110) 的合成原型，已接入真实星图程序并完成下述 Windows 隔离验证。当前是**安装器与离线验证候选**，不是可替换现用星图的正式安装器。

没有覆盖真实安装、卸载旧星图、清除真实用户资料、操作游戏或发布正式版。二楼 PR #109 / `142001C8` 源码与原 Setup 保留，游戏内效果仍待验收。

## 候选与来源

本机中文产物相对于 checkout：`work/qtifw-real-trial-20261108-r12-zh/Xingtu-QtIFW-Local-Trial-Setup.exe`。此前 r10 的默认向导仍为英文，保留为历史证据，不再作为当前交付包。

| 字段 | 实际值 |
| --- | --- |
| 安装器源码 | `19973a5ca506a8b5d63b6597527d1e5af536735c`，构建开始时为干净远端提交 |
| 程序源码 | `142001c884029a8fb9dc08d9760e5149b9cb72da`，原冻结程序，未改标为新源码 |
| 程序版本 | 1.1.2 / 1.0.0.24，指纹 `142001C8` |
| QtIFW | 官方 Windows 4.11.0 / Qt 6.9.3；工具来源与校验见第一轮交接 |
| Setup 字节数 | 322,565,480 |
| Setup SHA256 | `1e053758c54af6bac4565721e6a58a32c4612529f279d266f95b6446dd1a9531` |

QtIFW 组件 1.0.0 / 更新测试 2.0.0 是试验编号，不是星图产品版本。`qtifw-build-record.json` 的 `installed=false` 是构建时状态；安装/卸载结果在独立验证回执，未改写构建记录。

复用原 `validate_managed_runtime_reuse` 和 `write_installer_program_inventory`：3,648 项程序文件包含三个 helper、watcher、只读 candidate 和 editor-base；不打入 work、活动资源、导入记录或用户自建文件。后续包从已核原 Setup 导出相同程序 archive，重建控制脚本及支持工具，避免重复冻结或改写原程序。

## 试用范围与资料选择

- 每个候选有独立 QtIFW 安装名称和试用 ID。支持工具的固定策略只允许本候选根下的 `targets`；旧星图目录不在可操作范围。
- 布局为 `<target>/program/星图.exe`，应用数据仍在 EXE 旁的 `<target>/program/work`。不会自动启动星图、创建普通程序启动快捷方式或启用游戏 watcher。
- 维护器在卸载汇总前询问：**是：保留（默认）／否：清除／取消：退出**；汇总再次显示范围。清除只处理本测试安装的 `program/work`，保留协作锁、外部文件及恢复副本。用户地图所在的 `assets/custom-maps`、`_internal/assets/custom-maps` 不在清除范围；程序拥有的只读默认资源随程序卸载。
- 清理复用资源写锁和现有同句柄 Windows 删除原语；链接、占用租约、未完成资源事务会阻止对应操作。QtIFW 可能忽略撤销错误而返回 0，因此同时核对 `trial-data.json`；完成页也会明确提示资料处理未完成，不能仅凭进程退出码称成功。

**普通 EXE 的生产单实例身份没有改变。** 不要直接双击内部 `星图.exe` 作为并行游戏试用。本轮运行验证只使用既有 `--probe-output` 独立 IPC/禁用游戏监听入口，以及 `--self-check`。常规并行运行身份仍是正式接入的缺口。

候选默认目录留有合成测试痕迹。需要全新安装场景时，只能在其固定范围内使用新名称；不要用 `--root`，QtIFW 会在控制脚本构造后才应用该参数。使用构造前可读的参数，例如：

```powershell
& '<候选 Setup>' --reject-messages --confirm-command install 'TrialTarget=<候选根>/targets/manual-trial'
& '<测试目标>/trial-maintenance.exe' --reject-messages --confirm-command purge DataChoice=keep
# 仅专用测试资料：将上面的 DataChoice 明确改为 clear。
```

## 已执行的验证

| 路径 | 实测结果与边界 |
| --- | --- |
| 真实新装与程序核对 | 3,648 项文件逐字节通过，维护器生成；默认目标及独立指定目标均执行过 |
| 完整客户端离线探针 | 56 项断言通过；资源自检通过；4 个 S0/S1 模式上下文可用；没有游戏枚举、原生输入、截图或线上更新 |
| 辅助程序 | import/recovery/reset 的 `--help` 均退出 0，watcher `--status` 退出 0；这些是入口/依赖检查，不是实际用户导入或重置事务验收 |
| 更新故障 | 真实程序组件更新时 Execute 故意退出 47；安装器退出 3，旧程序没有自动恢复，资料及维护器保留。即使传入 DataChoice=clear，更新仍不清理资料 |
| 明确前向修复 | 维护器从已知本地仓库 `install org.xingtu.qtifw.realtrial` 成功；3,648 项文件及 `list` 的组件版本再次核对通过 |
| 保留、清除与重装 | 保留退出后资料存在；同目录重装通过；清除回执成功。最终候选又验证只清除测试 work，两个用户地图目录与外部哨兵字节保留 |
| 硬中断与重试 | 提取前、提取后均停止自行创建的安装器 PID；无维护器的原目录可重试。提取后 3,648 项残留程序先移入 `recovery-files` 保留，再安装并核对，资料不变；新维护器可再卸载 |
| 准入反例 | 无效资料选择、活动资源租约、hardlink 资料阻止真实维护器卸载，程序/资料保留；晚到的 `--root` 覆盖被拒，指定的范围外测试目录未创建 |
| Inno 夹具迁移 | 真实注册表记录绑定随机 AppId 和路径；同目录保留、新目录复制后清除新副本均通过，旧数据副本保留。Qt 安装故意失败后，用已知旧测试 Setup 重装恢复夹具 |
| GUI 与取消 | 默认保留可见；No 映射到 clear，汇总范围与选择一致；取消后程序仍逐字节完整。另验证选择过 clear 后取消，再运行未指定选择的 CLI 卸载仍默认 keep |
| 源码回归 | `python -m unittest discover -s tests -p 'test_qtifw*.py' -q`：21 项通过，无 Windows skip；含实际 filesystem、junction、hardlink、租约、重复恢复与未发布临时记录保留 |

中断支持子进程有界自行退出，只终止本轮创建的安装器进程，没有按进程名停止程序。以上不是断电或游戏验收，也不是原子自动回滚承诺。

## 证据绑定与已修正的失败

完整原始记录保留在本机 `work`，未上传原始 Windows 日志、用户路径或二进制：

- `qtifw-real-trial-20261108-r6/validation/result.json`：完整 CLI/客户端/更新/中断/迁移流程，`completed=true`。安装器源码 `f3629c1`，其实际验证脚本内容随后纳入 `4b3152b`。
- `qtifw-real-trial-20261108-r10/gui-validation`：最终包默认目标新装核对及原生向导观测；`gui.stdout.log` 记录 `TRIAL_GUI_DATA_CHOICE: clear`。`scope-clear-validation/result.json` 记录取消后 3,648 文件不变、后续 CLI 清除及两个地图目录保留。
- `qtifw-real-trial-20261108-r8/default-after-cancel/result.json`：取消 clear 后 CLI 默认 keep。此处资料选择与操作代码和最终包相同。

r7 至 r10 的后续变化只接入控制脚本中的 GUI 选择、汇总文字与 Windows 深色系统下的页面可读性；r10 补验默认新装、字节核对、GUI/取消和清除范围，未把 r6 的完整流程宣称为 r10 全部重跑。程序 payload、支持工具的恢复/清理函数及组件依赖操作保持相同，程序全清单复核约束继续生效。

中文修订 r11/r12 明确配置 `ifw_zh_CN` 与 `qt_zh_CN`，框架页面和标准按钮共用内置简体中文翻译，不依赖系统区域格式或启动参数。修订包含中文产品文案、去除欢迎页技术标识、隐藏没有实际快捷方式操作的开始菜单页面，以及中文链接宽度和原生 Modern 向导布局。机制见 [QtIFW 官方翻译配置](https://doc.qt.io/qtinstallerframework/ifw-customizing-installers.html#configuring-and-overwriting-default-translations)。原英文实际窗口和日志在 `work/qtifw-zh-validation-20261108`；中文修订的实际 UI/安装证据在各自候选 `zh-validation`。r12 新装再次核对 3,648 项程序文件通过；欢迎、组件、汇总、维护器、资料选择及取消确认页面均实际观察为中文，两个 GUI 日志记录 `UILanguage=zh_CN`。取消后的程序清单再次完整。21 项源码回归通过。本轮没有重跑完整 r6 故障/迁移流程，不将旧证据改称 r12 全量实测。

目录选择仍为本机固定测试范围，不是面向用户的正式目录选择交互。用户指出它不方便；推荐目录和可浏览更改位置的设计仍待单独接入，中文修订未放开目录保护。

保留的中间失败包括：r1 把 RCC 当 QRC 导致附加工具未装载；r3 发现 `--root` 时序，未提取程序；r5 的首次客户端探针遗漏测试写入标记被保护拒绝，补齐自身测试前提后重跑；同轮撤销顺序错误使资料回执缺失，未算通过。实测还证明信号回调抛错或 `interrupt()` 不能阻止卸载，撤销错误可能被框架忽略。修复分别放在原生操作前的准入、明确的组件依赖顺序和独立结果核对，没有放宽原断言或删除失败记录。

恢复只接受本试用归属与原程序清单；完成态丢维护器且仍有程序文件时拒绝盲目接管。没有复制自建整树备份/回滚引擎。界面选择由控制脚本 ready-page 生命周期负责，避免依赖卸载器不触发的组件页面加载时机。

## 正式替换前的剩余工作

1. 独立常规运行身份、正式安装目录/版本/快捷方式与 watcher 生命周期接入；当前不能用离线探针代替日常游戏试用。
2. 真正旧星图的识别与迁移适配器。当前只执行随机身份的 Inno 夹具；两项合成 work 文件的复制不代表真实地图、作者回执、外部编辑器资料和 pending 恢复合同都已迁移。未执行生产 AppId 的卸载器。
3. 安装/更新过程中的用户取消、断电重启、不同 SID/UAC、并发安装器及任意 IO 异常还未完整覆盖；不宣称任意失败自动回滚。正式分发材料仍需单独完成。

重新复现需全新输出根：先运行 `tools/build_qtifw_trial.py --help` 构建，再运行 `tools/probe_qtifw_real_trial.py --trial-root <全新候选根>`。已有证据目录不可覆盖；本轮使用当前管理员进程上下文。

官方接口参考：[QRC 附加资源示例](https://doc.qt.io/qtinstallerframework/qtinstallerframework-dynamicpage-example.html)、[操作与 UNDO](https://doc.qt.io/qtinstallerframework/operations.html)、[控制脚本生命周期](https://doc.qt.io/qtinstallerframework/noninteractive.html)、[资料选择对话框](https://doc.qt.io/qtinstallerframework/scripting-qmessagebox.html)。
