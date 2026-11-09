# PR #125 Windows 原件验证（2026-11-09）

PR 原件的 WPF 窗口已在本机实际显示、操作并完成失败后恢复。另制的隔离实验 Setup 安装成功，安装后原件完成 31 次新进程启动。**这是阶段一合成网格切片的验证，不能解释为完整 Python 客户端已迁移或实战验收。** [脱敏数据与启动原值](evidence/pr125-windows-validation-20261109.json)

## 源码与新增范围

- 仓库：`chenhuanh9-a11y/xiaochao-assistant`，PR #125，head `29dd3f24cdaf46f79b9ce401ab5b0884b45194af`，分支 `cursor/csharp-phase1-skeleton-6c9f`；base/main `7991dbbecdb66d76799415fb555d2c8a0361acc6`。
- 2026-11-09 13:39:33 +08:00 在新隔离 clone 完成 detached checkout；阅读实际 43 文件差异后才构建。结束时远端 head/base 未变，原件 Git 状态干净。托管 worktree 请求未返回，未观察到注册产物；使用独立 clone，没有动旧实验或正式源码。
- PR 有 `StarMap.App` WPF、Core、CLI 和合成 fixture；原文明确阶段一**不制作安装器**。本轮没有替换 WPF 或修改原件代码。额外增加独立安装脚本/说明/来源记录及只读启动观察器，全部留在原件目录以外；不把旧 hybrid 原型代替本 PR。
- 安装器补件版本 `0.125.0.1`，独立 AppId/安装目录/卸载注册和用户开始菜单组，无自动运行、关闭其他程序、关联、依赖安装或数据迁移。该版本只标识实验安装器，不是产品发布版本。

## 构建与环境

Windows 11 Pro 10.0.22631，Intel Core Ultra 7 265K，20 逻辑处理器，物理内存 33,780,793,344 bytes；采样窗口 DPI 96。用户后台程序保留，没有重启或清理系统文件缓存。

原件 `global.json` 禁止 preview。机器原有 SDK 为 8.0.400-preview.0.24324.5，故在实验目录解压微软官方 SDK **8.0.427**，验证官方 SHA-512 与 Microsoft 签名；仅给任务进程设 PATH，没有修改系统 SDK 或原件 `global.json`。自包含 App 使用 Microsoft.NETCore.App / Microsoft.WindowsDesktop.App **8.0.33**。

在实际 App、CLI 和 Core.Tests 项目目录分别执行 `dotnet build-server shutdown`、裸 `dotnet build`，均 0 warnings / 0 errors。App 发布命令（`<publish>` 是独立证据目录）：

```powershell
dotnet publish -c Release -r win-x64 --self-contained true -p:PublishTrimmed=false -p:PublishSingleFile=false -o '<publish>'
```

CLI 另以 `dotnet publish -c Release --self-contained false` 形成原件比较脚本要求的 Release DLL。原件 Core.Tests 独立程序 `dotnet run --no-build` 的 35 项检查通过；这不是另写的一套替代测试。

原件 `tools/phase1/compare_grid_slice.py` 实际调用本 checkout 的 Python `sample_edges` / `_grid` 和本 checkout 的 Release C# CLI。使用隔离 Python 3.13.3 / NumPy 2.2.6 / OpenCV 4.12.0；系统 Python 的 NumPy 2.2.5 不符合锁定版本，未拿它运行对等比较。black-frame、prior-local、global-large 及 prior-local --cancel 共 **4/4** 通过，点字节与 canonical 结构 SHA-256 严格一致，没有改阈值或容差。均为 PR 自带合成输入。

## 真实 WPF 操作

先启动**原件自包含 Release publish**，通过实际界面输入目录并点击按钮：global-large → black-frame → 不存在目录 → prior-local 恢复。

| 输入 | 现场结果 | 会话序号 |
|---|---|---:|
| global-large | grid，300 / 8，结构哈希与参考一致 | 1 |
| black-frame | fast-insufficient-edges，0 / 0 | 2 |
| 不存在目录 | 无法读取录制帧，旧结果文本被错误提示替代 | 保持 2 |
| prior-local | grid，102 / 8，恢复且结构哈希一致 | 3 |

四次操作都有本地窗口截图与稳定回读文字。黑帧仍显示 `接受 Accepted`，这是 ResultGate 对生命周期 ticket 的接纳，**不是识别/位姿成功**。按钮计算同步运行在 UI 线程；握手是 constructor 内的同进程静态能力检查，没有独立后端进程/IPC。

安装后 UI helper 将仍运行的便携窗口归入新安装的同名 exe 项，owner 校验失败；没有据此继续输入。核对自建原 PID 的路径和启动时间后，仅正常关闭该测试进程。后续启动观察器按自己新建的 Process/PID 限定窗口，不靠标题或 exe 名匹配其他程序。

## 体积与隔离安装

全部是递归文件的逻辑长度之和，MiB = bytes / 1,048,576，不是 NTFS 分配空间；Setup 不是 ZIP。

| 产物 | 文件数 | bytes | MiB |
|---|---:|---:|---:|
| PR 原件 self-contained Release publish | 466 | 167,852,380 | 160.0765 |
| 补件安装 payload | 476 | 167,905,808 | 160.1275 |
| 实际 Inno Setup 文件 | 1 | 62,410,732 | 59.5195 |
| 实际安装目录（含卸载器） | 478 | 172,463,921 | 164.4744 |

Setup：`StarMap-PR125-Stage1-Supplement-rev1-win-x64-Setup.exe`，SHA-256 `9522e299efc793efa0f6c6ca2ec66cfa20b9690e2a162f4ee2194ded0c1739ca`。

payload 保留原件非 PDB 文件字节，去掉 App/Core 两个 PDB 共 39,480 bytes，加入三个合成 fixture 的九个文件、来源记录与说明。内含 .NET 8.0.33 自包含运行时；**无 Python、WebView2 或真实地图资源**。不与此前含 Python/855 资源的 hybrid 安装包作同功能体积比较。

实际安装前目标与唯一 HKCU 卸载键均不存在；固定目标在新的 `pr125-validation/installed/StarMap-PR125`。命令使用该默认目标，没有 `/DIR` 覆盖：

```powershell
<Setup.exe> /VERYSILENT /NORESTART /SP- /LOG="<isolated install log>"
```

安装 exit 0，日志记录成功、无需重启。安装后 476 个 payload 文件 SHA-256 全部一致；增加的卸载器 exe/dat 合计 4,558,113 bytes，解释安装目录的差额。Inno 同时创建本实验唯一的 HKCU 卸载注册及用户开始菜单快捷方式；不能说没有注册表写入。未覆盖正式安装或玩家数据，实验安装保留。

这只证明当前 Windows 的一次隔离新安装及运行，不等于干净 Windows、无已装依赖条件、升级、中断恢复、卸载或回退通过。

## 启动计时

额外只读 `StartupProbe` 启动**安装目录的原件 self-contained Release exe**，工作目录为该安装目录，不传业务参数。先独立校准 1 次（不纳入统计），再测 31 个新进程；首次单列，其余 30 次统计。所有 31 个进程窗口/UIA 端点齐全、正常关闭、exit 0，无残留；不是 31 次点击同一进程。

```powershell
StartupProbe.exe --exe '<installed>/StarMap.App.exe' --working-dir '<installed>' --output '<new evidence>' --count 31
```

t0 为 `Process.Start` 前 QPC。可见窗口端点：自建 PID 的无 owner 顶层 HWND，IsWindowVisible 且客户区尺寸大于零。UIA 端点：只读同时观察到会话、`协议 1  grid-slice-v1` 及 enabled 的运行按钮。它证明同进程 constructor 握手文字/按钮就绪，尚未实际执行输入，也不是独立后端就绪。

| 端点 | 首个新进程 ms | 后 30 中位数 ms | 后 30 范围 ms | 后 30 样本 p95 ms |
|---|---:|---:|---:|---:|
| CreateProcess 返回 | 5.3826 | 2.0785 | 1.7083–2.7282 | 2.5249 |
| 可见窗口 | 520.5106 | 521.67685 | 485.6866–572.0781 | 568.8820 |
| UIA 同进程握手/按钮 | 687.6180 | 687.53125 | 618.3661–785.4796 | 780.6983 |

p95 为 30 个有效后续值排序的 nearest-rank 第 29 项，只描述本样本。首个新进程在功能验证及校准之后，不是本机第一次启动该程序；系统缓存未清空，不能称冷系统启动。所有原值及缺测/退出状态在脱敏 JSON。

观察器以 5ms 间隔轮询、每进程 t0+10s 绝对截止。UIA、GDI 读取有观察开销；样本带着这些开销，不是无仪表性能。仅按自己创建的 Process 正常 CloseMainWindow；5s 不退出就停止并保留进程，未调用 Kill。

GDI 客户区/窗口截图已保存和检查，但首个 BitBlt 或非均匀 RGB 只作为候选读回，不能可靠证明 WPF/DWM 物理首像素。`firstVerifiedRenderedClientPixelMs=null`；不拿 input-idle、进程存在或候选读回来填这个端点。

## 保留缺口

PR 不含完整 prepare/refine/propose、真实帧解码与识别、地图身份/楼层、结构 overlay、作者路线或游戏热键接入。不能将当前 0.69s 壳就绪与旧完整 hybrid 的首结构显示作同功能快慢比较。独立复现所需的观察器/安装脚本、二进制、截图和原始日志没有随公开报告上传。

原小时级 hybrid 稳定性任务在本轮启动计时期间未启动，未混入此 PR 数据。干净 Windows 升级/中断/回退仍缺可恢复环境；本机只有已停止的 Ubuntu WSL2，不能代替 Windows 来验收 WPF 安装。未启用 Hyper-V/Sandbox、改变安全设置、发布/合并产品或部署服务。
