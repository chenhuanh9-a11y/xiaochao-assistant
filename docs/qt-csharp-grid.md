# Qt + Python 产品的 C# grid 局部优化

## 范围与起点

当前工作区 `E:\codex\worktrees\qt-csharp-hybrid\星图`，分支 `codex/qt-csharp-grid-20261110`，起点为正式 Qt 基线 `cc5100c6e3cea425ac4dbed82df92623754f81fa`。本任务只优化 `_grid` 网格候选计算及必要通信、进程恢复、打包兼容和验证，不是框架迁移。原 Qt UI、捕获/输入/浮层、设置与资源操作继续使用，不为本任务迁移或重做。

旧 WPF 工作保留在 `codex/wpf-python-hybrid-20261110` / `d836b00c18f806324f2a5489d3665ab755c02598`，其未提交文件原地保留。WPF 主页、原生显示/键盘及其后续参考窗/提示任务停止推进；该分支的 `hybrid/MIGRATION.md` 只作历史记录。纯 C# 路线独立，不纳入本分支。

## 实现边界

- Qt 仍负责原完整产品交互，Python 仍负责 rank/识别、refine、最终 pose、当前帧验证/接受拒绝、结构渲染与资源核心。C# 只返回数值候选，不授予地图身份、位姿或显示权限，两模式共享原 Python 核心。
- 可复用旧成果的持久 protocol 2 C# 服务、数值修正、Python bridge 和定向测试。旧回放、模拟、WPF 或 CI 证据只说明原来对应提交和入口，不能当作本次 Qt 生产入口已通过。
- 先核对真实同步/后台调用与唯一进程所有者，再接入 Qt。必须明确服务失败时本请求如何结束及下次如何恢复；没有实现并验证的自动 Python 回退不得声称存在。

## 执行与验收

1. 核对正式入口、两模式求解与后台线程、取消和退出写者；携入必要 C# 服务与 bridge，排除 WPF 依赖。
2. 接入原 Qt 生产流程并保留原 Python 同输入对照方式。验证实际候选进入后续 refine/最终消费者，不只测一个更容易的替代入口。
3. 针对性验证候选/最终结果等价，取消、超时、进程故障/重启、正常退出与整个自有进程树资源释放。按影响验证 Qt UI 与原产品链路，不扩大为无关模块重写。
4. 同输入、资源、依赖及冷/热条件测量计算与端到端性能。首贴约500ms、后续开图约300ms是既有目标，不以放宽正确性达成；保存帧没有真实按键端点时不冒称完整延迟。
5. 从同一最终远端 commit 干净构建 Qt+Python+C# Setup，记录资源/依赖/版本、精确字节数与 SHA256，检查安装兼容与进程收尾。未经授权不安装、发布、恢复采样或向游戏发键。

当前布局证据仅用2026-11-05及之后有来源的帧，不注入地图/位姿答案；保存帧、合成、桌面、实机、安装分开。实际游戏链路与最终交付未验时不称优化交付完成。

## 当前状态

Qt 正式入口增加 `--grid-backend csharp|python`，默认 C#；直接构造测试用 `QtClient` 仍默认原 Python，便于既有隔离 fixture。比较必须明确记录从哪个入口选择了哪个后端，不把直接构造的 Python 测试称为 C# 生产入口验收。源码运行前先 Release 构建 `hybrid/StarMap.Grid`；最终打包仍需携入自包含 C# 组件，当前尚未实现打包接线或 Setup，不得用现有打包脚本冒称成品可用。

QtClient 持唯一 `CSharpGridRuntime`，显式传给原 LiveRuntime 及所有 `_AlignmentHost`。实际 Session worker 的整个业务计算持一个 request scope，每次 fit 复用该作用域；ContextVar只替换 `_grid`，其余求解和原资源完全不变。S1 scanner同步进入同一worker；它会吞掉preferred candidate异常，因此grid失败须保留至整次请求并进入当前性取消，不能漏掉失败候选后改称另一张被唯一确认。nested fit不得把派生取消回调重新加入外层，避免相互递归。原 `shutdown_client` 在Session取消/排空后关闭服务，普通关图不关闭常驻进程。

当前策略：取消/超时/计算或协议错误拒绝本请求；失败进程被明确收尾，下次显式请求可启动新进程。没有自动 Python 回退。Python对照只有明确选择 `--grid-backend python`。

已有 .NET SDK10.0.403 Release构建0警告0错误。携入的三个C#源文件Git blob与d836b00一致；不是另写新算法。匹配Python3.13.3/OpenCV4.12/NumPy2.2.6下原服务/候选消费13项通过；owner的5项覆盖真实proposal、nested隔离、派生取消、sticky失败、等待取消、活跃关闭及显式重启。

匹配Qt6.11.1环境的联合115项通过（`work/qt-grid-joint-final-20261110-01.txt`），含原Session/LiveRuntime/partial-cleanup96项。S1 scan/fit diagnostics与原Qt退出另36项通过（`work/qt-grid-s1-shutdown-20261110-01.txt`）。实际Qt worker集成回归使用明确合成捕获/地图身份/楼层与临时prepared资源，原Session→LiveRuntime→Host→C# grid→原Python后续验证均执行；两模式分别重复捕获复用同一PID，拒绝显示与live授权，最后从原shutdown_client排空并验证子进程exit0。该集成及owner末次6项见 `work/qt-grid-ingress-cleanup-final-20261110-01.txt`，不是游戏验收。

初次接线测试先发现Event需转换为回调和nested取消递归，均已修复；随后fixture因缺fast prepared产品而在原入口跳过grid，现使用原prepare生成真实临时npz，没有替换solver或改观察范围/断言。原失败日志保留。系统Python有数值依赖漂移，子任务系统环境探查不作权威结果。

独立复核发现协议异常可能绕过整请求失败：`rows=null` 的计数和字符串score的数值检查曾抛TypeError、未设sticky标记，preferred候选异常被吞后外层能正常退出。现先验证匹配ID、布尔终态和候选数值/平移，再记录计数；类型异常作为GridFailure进入原收尾。新反例只替换通信，真实proposal/owner继续执行，并检查吞掉错误后仍取消及整请求失败。修后服务/bridge/owner/Qt接线20项通过，日志 `work/qt-grid-protocol-final-20261110-01.txt`；没有放宽原生产断言。

仍待：审后差异复核、原正式Qt入口/当前真实帧的同条件回放、S1生产入口代表样本、计算与端到端性能、打包组件/Setup以及经授权的真实游戏链路。未合并、发布、安装、恢复采样或发键。
