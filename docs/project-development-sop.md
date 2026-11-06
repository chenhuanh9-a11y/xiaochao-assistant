# 星图项目开发 SOP

本手册用于把需求变成可审查、可复现、可交付的星图改动。默认分工是：**云端隔离开发、并行审查与可运行回归，本机 Windows 做原生验证、Setup 打包及受控安装升级验证**。软件交付只用 Setup；源码交接包和地图导入 ZIP 不属于便携软件交付。

维护日期：2026-11-06。本次仅整理文档，不合并产品代码、不构建或发布新版本。版本、分支、测试结果的当次事实在[基线与远端分支台账](maintenance-baseline-20261106.md)；持续维护范围和分支拆分在[功能模块维护表](maintenance-modules.md)。不要把本手册中的命名建议、历史测试或样例参数当成已完成动作。

## 1. 入口与文档优先级

1. 本次用户范围、项目根 [AGENTS.md](../AGENTS.md)、实际执行环境的限制。
2. 当前 checkout 的代码、版本、资源锁、实际调用链及本次证据。
3. [development.md](development.md)、[Windows 公告规则](windows-update-notice.md)、对应模块文档及[PR 模板](../.github/pull_request_template.md)。本手册负责串起这些入口，不替代它们。
4. 历史发布记录、README、旧候选文档只作溯源。比如 README 仍介绍首次公开 1.0 基线，不能据此认定当前客户端只有 Tk 或当前版本仍为 1.0；`experiments/qt_quick/` 已被真实 Qt 打包入口消费，也不能因目录名而排除维护。

当文档、flag、截图与实际代码不一致时，记下矛盾并查清，不用提高能力声明、删除验证或改测试预期来掩盖。S1 的生产候选显示、私测候选淘汰、人工选择、唯一身份、静态控件观察和实时能力是不同合同。

## 2. 一次任务从哪里开始

普通小修在 PR 填以下六项即可；跨模块协议或状态归属变更才写额外设计，不为每次改字建立大流程。

| 项目 | 最少要写清的内容 |
| --- | --- |
| 问题与用户路径 | 谁在什么赛季/难度/页面/安装版本，做了什么，期望什么，实际什么 |
| 范围与停止条件 | 本次修什么、不修什么；是否涉及资源、设置、协议、构建或发布 |
| 基线 | 完整源码 SHA、远端分支、工作区、是否干净；截图/日志/包所属版本 |
| 影响链路 | 入口 → 状态写者 → 页面/缓存 → worker → 最终像素/落盘/退出消费者 |
| 验收 | 必须通过的正反例、平台和输入；真实游戏/原生窗口/实际升级是否在范围内 |
| 调度 | 主负责分支、协作分支、共写文件负责人、集成顺序及交接物 |

先复现再修复：能自动化时保留“基线失败、改后通过”的最小回归；只拿到反馈截图时明确缺帧/真值/时间端点，不猜根因。独立查看消费端，尤其不能用“服务返回成功”“进度到 100%”“发出 quit 信号”代替列表更新、数据持久化或进程退出。

## 3. 分支与云端并行

### 3.1 推荐模型

- `main`：共同集成线。它可以落后于本机候选或发布分支，不能自动代表用户正在用的产品。
- `feat/<模块>/<任务>`、`fix/<模块>/<任务>`、`perf/<模块>/<任务>`、`docs/<任务>`：短期任务分支，每个分支只承担可独立评审的一件事。
- `integration/<候选>-<日期>`：必要时建立的组合验证分支，记录纳入/排除的精确提交；没有必要时不创建额外层。
- `release/<版本>-<日期>`：必要时用于最终版本、资源锁和打包绑定的冻结分支。它不是发布授权或已上线证明。
- `experiment/<主题>`：明确隔离的实验，不以重命名、放宽 flag 或直接拷目录进入正式构建。

模块是长期维护责任，任务分支是短期变更载体。**不建议为每个模块维持永久分叉的实现**，也不一次创建几十个空分支。模块表列出的建议分支只在任务启动时创建。现有 `codex/`、`feature/`、`cloud/` 等分支保留原名，本次不重命名、不删除、不强推；“已是 main 祖先”也不等于可以直接删除，先确认没有依赖和保留用途。

### 3.2 建立隔离工作区

以下是 Bash 模板，先把两个值换为本次核实结果；不在别人正在工作的目录切分支、reset 或 clean。

```bash
git fetch origin --prune
git status --short --branch
git worktree list
BASE_SHA='<核实过的完整40位SHA>'
TASK_BRANCH=fix/gallery/author-selection
git show --no-patch --format=fuller "$BASE_SHA"
git worktree add -b "$TASK_BRANCH" ../xingtu-gallery-task "$BASE_SHA"
cd ../xingtu-gallery-task
git status --porcelain=v1 --untracked-files=all
git rev-parse HEAD
```

也可新 clone 后建分支。本次 SOP 就在独立 clone 中完成，没有改动现有候选工作区。`fetch --prune` 仅更新本地远端跟踪引用，不是删除远端分支；其输出的历史 forced update 也不是当前任务做了 force push。

并行边界：同一源码文件同一时间只有一个写者。`app/identityv_daily_assistant.py`、`client_live_runtime.py`、`client_coordinator.py`、`Client.qml`、`client_settings.py`、`version.json` 和构建/资源锁是常见冲突点。需要多人改同文件时，先拆独立模块或按顺序交付补丁，由集成负责人逐段整合。审查者只读，禁止为了让自己的验证通过覆盖别人的实现。

### 3.3 正常提交与交接

1. 修改前后分别看 diff；运行与改动相称的检查。不要把无关全库测试作为每次小修的固定门槛。
2. 只 stage 本次文件，检查 `git diff --cached --check` 与 `git diff --cached --stat`。不提交私有素材、日志、设置、账号信息、凭据、构建目录或原始游戏数据。
3. 提交并普通推送自己的分支；再次核对 `git ls-remote origin refs/heads/<分支>` 与本地 `git rev-parse HEAD` 相同。提交、推送、PR、合并、构建和发布分开报告。
4. 需要 PR 时先建草稿，按已有模板写范围、调用链、实际命令/结果/未测项。新分支可以基于已核实的 release 线，但必须说明相对 main 继承了哪些代码，不把这些代码算作本次新增。
5. 集成后重新锁定最终 SHA，重跑受影响的组合接缝。合并前的绿灯不能直接变成合并后证据。分支移动后也不能沿用旧 SHA 的 CI 结论。

### 3.4 远端不含本机候选时

优先把允许同步的源码提交推到明确分支，再从该 SHA 建云端任务。若本次仍限制推送或只有快照：

- 记录本机完整 SHA、快照/补丁 SHA256、每个文件 before/after SHA256、实际字节数和换行规范。
- 云端快照没有 `.git` 就称“源码快照”，不能 `git init` 后把新 commit 伪装成原候选历史。
- 用精确基线核验和 `git apply --check`，再到独立候选应用。Git blob 与 Windows CRLF 落盘哈希分开记录；不以忽略所有空白或猜补丁尾部来跳过不匹配。
- 文件已变或新增冲突时停止那一项，取新基线逐段复核；不把整个旧文件覆盖过去。传输哈希匹配仅证明字节一致，不证明功能正确或有素材分发权。
- 回到完整 Git 来源后重新提交、运行接缝测试。若使用暂未推送的本机候选构建，清楚标为偏离“最终远端 SHA”标准的隔离候选，不称正式发布就绪。

## 4. 环境与命令证据

### 4.1 云端可以承担

代码盘点、设计、源码修改、fixture/纯数据合同、保存帧回放、资源 manifest/哈希校验、适配当前依赖的 Qt offscreen、补丁审查和 CI 分析。输入来源、系统、Python/依赖、真实与替身边界随结果记录。

项目的产品依赖在 [requirements.txt](../requirements.txt)，Qt 客户端测试依赖在 [requirements-client.txt](../experiments/qt_quick/requirements-client.txt)，Windows 构建依赖在 [requirements-qt-build.txt](../tools/requirements-qt-build.txt)。按当前锁定文件安装到独立虚拟环境，不在共享全局环境直接升级。只安装一种 OpenCV 分发包。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r experiments/qt_quick/requirements-client.txt
export PYTHONPATH=app:tests:tools:experiments/qt_quick
export QT_QPA_PLATFORM=offscreen
export QT_QUICK_BACKEND=software
.venv/bin/python -B -m unittest -v test_floor_contract
```

上面是环境建立模板，Python 可用版本和 wheel 需在所选云环境实查。本次没有重新安装依赖：复用了隔离的 Python 3.12.14、PySide6-Essentials 6.11.1、OpenCV headless 4.12.0.88、NumPy 2.2.6、Pillow 12.3.0、psutil 7.2.2。它可用于说明本次离线命令可运行，**不是 Windows x64 Python 3.13 产品构建环境**。

本次在文档基线实跑的命令：

```bash
python -B tools/build_qt_client.py --help
python -B tools/preview_windows_notice.py --help
python -B tools/build.py --help
python -B -m unittest -v test_floor_contract test_preview_windows_notice test_qt_installer_directory
```

这里的 `python` 指上述隔离解释器，使用所列 PYTHONPATH；最后一条 **20 tests，0 failures，0 errors，0 skipped**。这是文档示例与少量合同的可执行性验证，不是全库、最终 1.1.1 候选或真实安装验证。Windows 构建、发布和安装命令本次均未执行。

### 4.2 必须留在本机 Windows 的步骤

| 步骤 | 为什么不能拿 Linux 结果替代 | 输出证据 |
| --- | --- | --- |
| 原生输入、窗口绑定、采集、DPI 与显示 | Win32 hook/消息循环、前台身份、真实窗口与屏幕像素行为 | 系统/DPI/窗口状态、实际入口、正常与失败/取消结果 |
| Windows 诊断文件身份及轮换 | 独占句柄、FileDispositionInfo、重解析点/硬链接合同 | 原生用例与真实 skip 理由；不安全兜底不能算通过 |
| Qt Setup 冻结与编译 | 构建器明确要求 Windows x64 Python 3.13、锁定依赖、qwindows.dll、Inno Setup 6 | 原始 build-record、构建日志、release-report、Setup |
| PE/UAC/签名和包内自检 | 实际 PE 资源、Windows 权限、冻结依赖/插件 | manifest、版本、签名状态、self-check 结果 |
| 安装、覆盖升级、卸载/数据保留 | 真实 Inno、AppId、目录、注册表和快捷方式 | 前后快照、逐步结果、保留文件哈希、进程清理 |
| 真实游戏与端到端性能 | 物理输入、游戏画面就绪、采集和首次正确可见像素 | 获授权的真实流程与有来源真值；未执行就写未执行 |

Windows CI 可以补原生回归，但 workflow 名带 Windows 不表示在用户机器完成打包或游戏验收。本项目默认仍由本机承担 Setup 与实际安装升级；不得把 CI 测试 artifact 冒充最终软件安装包。

## 5. 跨模块开发不变量

### 5.1 身份、楼层、贴合和显示分开

- 噩梦/困难等模式共用识别、贴合与生命周期核心，差异由 season/mode 的 provider、楼层、坐标、入口和能力描述，不复制另一套算法。
- 资源可用不等于实时识别能力可用；静态保存帧观察不等于当前帧真实游戏资格；当前帧拟合通过不自动证明唯一地图身份。
- 作者图、自定义路线、图库预览是显示，不改变识别结构、身份、楼层与锁图权。静态参考、贴合层和独立预览分别跟踪各自显隐与代际，不互相借权。
- 相似组仅来自明确复核名单。共同主门、局部轮廓相近、当前候选池或“只剩一个”都不能自动登记生产组或绕过唯一身份合同。
- S0/S1 的楼层值、顺序、坐标、入口语义按真实合同读取。S1 B1–B4 与 S0 -1/1/2 不能硬套同一默认值；不为缺失侧门或坐标补假数据。

### 5.2 异步结果的所有权

顺着真实 producer → queue/worker → result → consumer 核对实际相关的 session/epoch、season/mode、map/floor、resource instance、capture generation、request token、opening/attempt 和 pool revision。不要机械添加不参与语义的字段，也不能只在生产端校验，消费前仍需确认结果属于当前请求。

关图、切层、切图、切季、重开局、改资源、退出后，旧结果不能重新显示、重新确认或覆盖新设置。隐藏页面不能阻止 worker 收尾，后台队列与样本必须有上限。普通关图是否保留同局历史证据，以当前产品身份/协议为准；历史证据不能直接显示旧帧，也不能把私测淘汰政策默认迁入 production。

### 5.3 UI、设置和持久化

页面与卡片保持稳定身份；重复选当前页/当前图应幂等。资源身份没变不重复扫磁盘/解码整库；普通状态按钮只更新所属区域。切页保留选择、焦点、滚动，恢复时及时显示最新公共状态。重任务在 worker，UI 写入归 UI 线程，最终消费者才报告完成。

设置写入测试包含保存成功后新服务重载、保存失败不伪装已生效、跨赛季/难度隔离、旧字段兼容；预览不暗中保存作者选择。导入/退出检查已接受写入排空，不能仅检查内存对象。

## 6. 资源与协议变更

1. 源码、授权素材、生成资源和用户数据分开。克隆仓库不等于有完整可运行图库；公开历史结构包也不等于当前完整 runtime 根。
2. 每批资源固定来源、用途/分发权限、schema、resourceVersion、season/mode/map/floor、大小/hash、坐标和 provider 兼容范围。先查[地图处理](map-processing.md)、[地图包升级](map-package-upgrades.md)、[S1 生产合同](s1-production-resource-contract.md)及[作者导入协议](s1-author-import-protocol.md)。
3. 使用已存在的 `tools/maps/` 和 `skills/update-helper-maps/` 入口及其 `--help`。历史 native24 配方中的固定数量和外部 renderer 前置条件不能当成适配任意新批次的全自动命令。
4. 新字段同时核对写者、loader/provider、缓存键、导入 helper、升级/删除、UI 最终消费者和兼容测试；作者显示 bridge 不得改写结构识别坐标。
5. 导入先校验路径、schema、大小/hash、包身份、完整 requiresFiles 与所有权，再进事务。未知/损坏收据、未完成事务、路径越界、符号链接/重解析点和无法证明的依赖失败关闭。
6. 旧数据“保护性保留”不等于获得导入/删除权。精确旧库存的领养与缺依赖恢复只能走当前源码认可合同，不从安装目录存在某文件就猜它有授权。
7. 安装/升级保留用户作者包、路线、选择、设置和 work；更新资源时同步失效相关缓存/旧任务。只刷新文案而继续消费旧资源是未完成。
8. S1 production release lock 同时绑定源码 payload、版本和资源清单。**纯文档提交也可能使源码 payload hash 变化**；本 SOP 分支不能直接拿原锁宣称可构建生产包。下一次真正发布需按现有构建器重新生成/核对精确锁，不能关闭锁或手改 build-record 迎合结果。

资源缺口按能力报告，例如“有图库 PNG，但无该模式 typed 结构/入口/楼层观察资格”，不要写成笼统“功能完成”。外部原图、游戏包、私有测试帧和凭据不进公开 Git 历史。

## 7. 验证分层与停止门

| 层级 | 允许证明的事情 | 不能外推的事情 |
| --- | --- | --- |
| 静态/单元合同 | 函数、schema、错误路径、状态策略、mock 接缝 | 真实 GUI、实际采集、资源全量正确 |
| 实际资源/保存帧回放 | 指定资源解码、明确输入下的判断、同帧对比 | 物理按键/游戏调度、新帧真实性、全部模式精度 |
| Qt offscreen/软件渲染 | 可实例化 UI、信号、布局与受控鼠标事件 | 实际桌面焦点、DPI、系统 hook、屏幕端到端延迟 |
| 原生 Windows 源码 | 实际窗口/系统 API 和所测用户路径 | 冻结包依赖与安装升级 |
| 冻结包自检/GUI | 成品模块/资源、该包实际启动和有限交互 | 真实游戏准确率、线上分发 |
| 安装/升级/卸载 | 指定来源和目标下的安装行为、数据保留 | 其他历史版本全部兼容 |
| 授权实机游戏 | 所测赛季/模式/地图/层及实际输入的结果 | 未测地图、长期稳定性、发布成功 |
| 发布回读 | 公告/按钮/渠道中确为目标产物 | 用户已经安装、所有客户端收到了提醒 |

每项记录命令、退出码、tests/failures/errors/skipped、输入来源、commit、平台和日志位置。模块独立进程更容易避免全局 Qt/Win32 mock 污染；不能把最后一条命令成功当成前面全部成功。`test_runtime_environment_diagnostics.py` 有自己的 FunctionTestCase runner，按 workflow 直接运行，空发现的 0 tests 不是通过。

回归至少选一个成功路径和风险对应反例：取消、失败、重复点击、关图/切层/切季、迟到结果、资源替换、退出中任务。具体模块参考维护表，不强制每个任务跑所有组合。已有失败用相同依赖和输入在固定基线复现，分别写“基线失败”“新增回归”“环境阻塞”“未运行”，不能删除失败来给总数凑绿。

现有 CI 在 `.github/workflows/`；联合 workflow 的分组和触发分支是实际代码，不保证任意新分支 push 都触发。PR/手动触发前检查当前 YAML、权限、目标 SHA、jobs 与 artifacts；对照组固定旧 SHA 只用于比较，不是候选证据。无 CI 结果时明确没有结果。

性能：首贴约 500ms、锁图并首贴成功后重开约 300ms 是目标，不是达标承诺。分别记录计算时长和输入接收到首次正确可见像素的端到端时长；连续样本报 n、p50/p95/max、未显示、错层和未知，写明百分位方法及真值来源。无像素端点时不称端到端，无真值时不补造零错层。

## 8. 冻结候选与本机交接

本机只消费一个明确的交接包：完整 source SHA/branch、变更范围、精确补丁/文件 hash、资源锁、依赖版本、云端检查记录、已知风险及所需 Windows 验收。多个并行修复先汇合成候选，不让本机每收到一点改动就重打包。

冻结检查：

1. 完整候选 SHA 与远端相符；没有冲突、未提交源码或未追踪源码。若本机例外候选未推送，单列缺口。
2. 已包含、明确排除、仍未验证三张清单，与实际 diff 和资源清单一一对应。
3. 同一候选完成必要联合回归；作者资源、设置/升级、两个前端共享链路与三个 S1 难度按影响范围覆盖。
4. `version.json` 显示版本、四段 `numericVersion`、runtime 兼容白名单、主程序/helper 与 installer 字段一致；不能只改文件名或版本展示。
5. 当前 production/private identity 与资源锁确认，输出目录全新，不覆盖旧包或现用安装。

构建开始后有新需求：保留已开始产物并写其 SHA 和范围；等待新修复集成后另建候选。不可偷偷将新源码/资源塞进冻结目录，或让同名包掩盖来源变化。

## 9. Windows Setup 实际操作模板

此节参数来自当前构建器并已核对 `--help`，**本次没有执行 Windows 构建**。路径与 SHA 必须换成当次真实值。先在隔离 checkout 中读取 AGENTS、版本、锁文件与脚本，不照抄历史绝对路径。

### 9.1 依赖、输入与计划

```powershell
py -3.13 -m venv .venv-qt-isolated
$Python = (Resolve-Path .\.venv-qt-isolated\Scripts\python.exe).Path
& $Python -m pip install -r tools/requirements-qt-build.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }

$SourceCommit = '<核实的完整40位候选SHA>'
$ResourceRoot = '<独立且获授权的实际assets/references资源根>'
$Inventory = '<与该根对应的实际资源清单JSON>'
$Iscc = '<本机已安装Inno Setup 6的ISCC.exe完整路径>'
$Output = Join-Path (Get-Location) 'work/qt-setup/<本次全新输出目录名>'
$Instance = 'production' # 只在本次范围确实要求生产身份时使用；私测按当前白名单另选

git rev-parse HEAD
git status --porcelain=v1 --untracked-files=all
& $Python tools/build_qt_client.py --check-environment --iscc $Iscc
if ($LASTEXITCODE -ne 0) { throw 'Windows构建环境不满足锁定要求' }

$BuildArgs = @('--source-commit', $SourceCommit, '--instance', $Instance,
  '--resource-root', $ResourceRoot, '--resource-inventory', $Inventory,
  '--iscc', $Iscc, '--output-dir', $Output)
# 仅当本次明确纳入展十困难内置显示默认时追加：
# $BuildArgs += @('--bundled-package', 'difficult-zhan10')
& $Python tools/build_qt_client.py --plan-only @BuildArgs
if ($LASTEXITCODE -ne 0) { throw '构建计划/资源/来源合同未通过' }
```

`--plan-only` 同样先检查 Windows 和依赖，不是可以在 Linux 跳过环境门的 plan。必须是资源实际根，不是其父目录；输入与输出不能互相包含，输出必须位于此 checkout 的 `work/` 或 `dist/` 的不存在子目录。构建器的 `remoteVerification` 明示未自行认证远端来源，远端 SHA 核对仍由交接承担。

### 9.2 冻结与包内核验

```powershell
& $Python tools/build_qt_client.py @BuildArgs
if ($LASTEXITCODE -ne 0) { throw '构建失败，保留日志，不宣称完成' }

$Record = Get-Content (Join-Path $Output 'build-record.json') -Raw | ConvertFrom-Json
$Record.source
$Record.buildCompleted
$Record.output
```

只使用构建器报告的真实 setup、stage 和 EXE 路径，不猜文件名、手写新记录或拿旧包改名。保存原始 `xingtu-qt-setup-build-v1` 记录与 `release-report.json`，核查 buildCompleted、clean、source tree、资源清单/hash、实际内置内容、Setup bytes/hash。必要时对真实主 EXE 再运行：

```powershell
& $Python tools/verify_admin_manifest.py '<构建记录对应的真实主EXE>'
Get-AuthenticodeSignature '<同一个真实Setup或主EXE>'
Get-Item '<真实Setup>' | Select-Object Name, Length
Get-FileHash '<真实Setup>' -Algorithm SHA256
```

主 EXE 必须 `uac_admin=True`、`requireAdministrator/uiAccess=false`；helper 是 `asInvoker`。这不证明 UAC 人工点击、快捷键或真实游戏已经通过。签名必须分别记已签/未签/未验证；编译成功不等于有签名。

源码/成品的 `--self-check --verify-resources` 由 `client.py` 提供不修改资源的检查入口；成品调用使用实际 EXE 和当前脚本支持参数，保留输出与退出码。冻结程序会创建自己的 `WORK_ROOT` 并写入 `client-self-check.json`，不是完全不落盘；从获准的隔离成品目录验证，不因此默认触碰现用安装。self-check 不开窗口、不监听输入，也不替代正常启动/退出 UI 验收。

`tools/build.py` 是旧的开发骨架，不是当前完整 Qt Setup 交付命令。`tools/rebuild_qt_installer.py` 仅用于脚本白名单允许且证明 runtime 完全不变的 installer-only 修复；源码、版本或资源变化走完整冻结，不用该入口绕过 release lock。

### 9.3 隔离安装、覆盖升级与卸载

先明确本次允许的测试目录和身份；不默认覆盖用户现用安装。production AppId 是实际升级标识，单改安装目录并不能保证注册表/快捷方式隔离，不能把生产身份测试称为“天然无影响”。依据当前 Inno 脚本和已有受控 harness 准备快照/恢复，不在 SOP 发明通用静默安装或改注册表命令。

最低验收表按本次影响选择：

- 全新安装：主程序、helper、Qt 插件、版本、中文路径、快捷方式、正常启动/退出。
- 上一真实已发布版 → 当前候选：设置/自定义路线/导入包/用户 work 保留，资源变更按精确合同迁移。
- 受支持的更老版本或缺收据场景：已认可库存与未知/改动/损坏库存分别验证，未知不能自动领养。
- 同版本测试候选 → 新候选：若本次确实使用同显示版本，记录两个 source SHA/Setup hash，不能用版本号判断文件相同；不据此宣称可重发线上公告。
- 已存在作者图/依赖/未完成事务：保护用户数据，明确阻断或可恢复状态；失败后不冒称升级成功。
- 卸载：核对程序清理与用户数据保留合同、进程退出、快捷方式/注册表前后状态；保留文件 hash 要在最终步骤后再次复核。

每步写成功/失败/超时/结果未知。强杀测试进程只是清理，不是退出验收通过。没有实际走过的升级路径留未测，不能从模板/文件投影单元测试推断全版本兼容。

## 10. 发布与线上验收

严格沿用 [windows-update-notice.md](windows-update-notice.md)，不另建发布入口。开始发布前先恢复最近真实最终公告和实际已发包基线；标签、main、最新本机测试包都不能自动替代它。

1. 固定最终 source SHA、资源与 Setup hash，核验公告只写此包相对上次实际发布包含的变化。
2. 在 Git 忽略的根 `notes.txt` 准备公告，使用[模板](windows-notes.template.txt)。含标题、版本、夸克和 UC 链接合计 ≤600 字符，无署名，没内容的栏目省略。
3. 使用真实原包和原 build-record 预览。Qt 记录必须传冻结包的完整 `--source-commit`；工具仓库具有该 commit 对象且版本一致，不手改记录去匹配已移动产物。

```powershell
& $Python tools/preview_windows_notice.py --installer '<真实Setup>' `
  --build-record '<原始build-record.json>' --source-commit '<冻结包完整SHA>' --notes notes.txt
```

4. 展示对外版本、线上值 + 1 的内部 `versionCode`、真实 bytes/hash 和公告全文；`versionCode` 与 `numericVersion` 不同。未取线上值时保持待定。
5. **用户亲自将同一个 Setup 上传夸克与 UC，明确回复本次“已上传”后**，才进入当前已授权的通知发布。一般自主执行指令不取消这一专门门槛；不替用户上传网盘，不安排绕过门槛的自动发布。
6. 唯一写入口是 `tools/publish_windows_notice.py --installer <同包> --notes notes.txt --publish --yes`。管理员口令由持有人按原文档自行设置，助手不读取/检查/打印秘密，也不把口令写到交接或命令参数。
7. 回读公告正文、`downloadPageUrl`、`ucDownloadPageUrl` 三处，再核渠道实际下载文件的版本、大小/hash。正文正确不保证独立按钮正确。

发布请求结果未知先重新 preview/回读，不直接再次 POST。401 不反复试口令。已发通知不能改删，也不能只加内部版本号对同显示版本重新发更改内容；发现错误如实报告，按当前规则处理更高版本修正。

状态用词固定区分：源码完成 → 检查完成 → Setup 已生成 → 安装/升级已验证 → 双网盘已上传 → 渠道可下载 → 通知已发布/回读。只报告有证据的那一步。

## 11. 回滚、故障与后续维护

- 未合并：保留问题分支和失败证据，返回已验证候选或另开修复分支，不污染他人工作区。
- 已合并未发布：优先用可审查的 revert 或新修复提交恢复，不强推重写共享历史；重新运行受影响接缝。
- 已构建未分发：保留旧候选产物/hash，将问题候选标为不可交付，修复后用全新输出目录重新构建。
- 已分发/已安装：先确认用户数据与资源 migration 是否允许回退。旧 Setup 不自动等于安全降级；先备份/隔离验证兼容，不能删除数据让旧版“能启动”。程序包回退、资源回退与用户数据恢复分开决定。
- 已发公告：遵守不可改删和版本递增约束，不冒充已经撤回用户通知。保存受影响版本、已验证补救方法和未解决风险。
- 资源事务中断：按已存在 journal/backup/recovery 合同恢复；不手删收据/锁文件绕过阻塞，不把未知完整性报成成功。

每次任务结束更新本次 PR/交接：变更、source SHA/远端确认、实际测试、成品身份、验证边界、遗留风险和下一步负责人。长期职责与新增模块变更同步到维护表；当次版本状态留在日期化记录，不把旧候选状态写成永久策略。

## 12. 可直接复用的任务与交付短模板

```text
任务：
用户路径与验收：
基线 SHA / 远端分支 / 工作区：
本次范围 / 明确排除：
模块负责人 / 共写文件 / 依赖分支：
入口 → 状态 → worker → 最终消费者：
资源/协议/设置影响：
测试命令、平台、输入、通过/失败/跳过/未运行：
独立审查与仍未验证：
最终提交 / 远端核对：
Windows 交接（如需）：
Setup / 原始构建记录 / bytes / SHA256 / 签名（如有）：
安装升级 / 游戏 / 发布分别处于哪一步：
遗留风险与下一步：
```

更多细节只在确有风险时补充；本手册本身不引入新流程工具、不创建持续后台任务，也不授予合并、发布、操作游戏或修改现用安装的权限。
