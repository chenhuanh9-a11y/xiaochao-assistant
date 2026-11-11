# 完整游戏诊断画面：本轮检查记录

基线 main `9b49db4`；独立分支 `agent/log-fullscreen-1-evidence`，PR 目标 main，保持草稿。范围为「游戏画面采集」「排查与帮助」，「设置与主界面」仅帮助文案。相邻「地图识别」「楼层判断」「路线贴合」不改算法、输入或显示授权。本任务不接通上传、不上线反馈、不合并、不打包或发布。

## 验证环境与入口

Windows / Python 3.13.3 / OpenCV 5.0.0 / NumPy 2.2.5 / PySide6 6.11.1。所有写入、容量轮换、过期清理与导出仅使用测试临时目录，未读写真实游戏截图、用户安装或已有诊断数据。

```powershell
$env:PYTHONPATH='experiments/qt_quick;app;tests'
$env:QT_QPA_PLATFORM='offscreen'
$env:TEMP='E:\codex project\tmp'
$env:TMP=$env:TEMP
```

下列模块按既有 Windows 检查方式分别执行 `python -m unittest <模块名>`，避免跨模块共享进程全局诊断序号。第一次合在同一进程运行时，旧序号测试期望从 1 开始、实际为已有进程序号；分进程运行通过，未修改生产序号。旧的局部图断言随本轮要求更新，取消/前台/绑定/失效拒绝仍保留。

修前/修后同输入对照：从 `git show 9b49db4:experiments/qt_quick/client_failure_samples.py` 隔离加载旧写入器（不替换工作区文件），向旧/新写入器各提供同一 53×37 BGR 合成帧及合法控件 ROI `(2,3,10,11)`，使用相同现行 Windows 存储层和临时目录。旧 PNG 回读为 10×11、完整像素比较失败；新 PNG 回读为 53×37、逐像素比较通过。这个对照仅替换诊断写入器，证明裁剪缺陷；不代表真实捕获或识别验证。

| 模块 | 结果 |
| --- | --- |
| test_client_capture_full | 11 通过 |
| test_client_capture_compat | 7 通过 |
| test_client_recapture_focus | 26 通过 |
| test_client_game_focus | 3 通过 |
| test_capture_preparation_privacy | 3 通过 |
| test_capture_environment_observation | 2 通过 |
| test_diagnostic_export | 3 通过 |
| test_diagnostic_evidence | 10 通过 |
| test_diagnostic_redaction | 7 通过，含新增来源/限额及敏感文字检查 |
| test_client_diagnostic_integration | 11 通过 |
| test_client_problems | 7 通过 |
| test_field_failure_samples | 8 通过，实际 dispatch 双开采样仍只采一次、保存一张 |
| test_client_info | 15 通过 |
| test_client_season_failure_export | 5 通过 |
| test_diagnostic_noninterference | 15 通过 |
| test_automatic_evidence_rotation | 22 通过、1 项 POSIX 专用检查跳过；Windows 句柄对抗检查执行 |
| test_failure_capture_terminals | 5 通过 |
| test_diagnostic_session_association | 10 通过 |
| test_diagnostic_disk_privacy | 2 通过 |
| test_automatic_failure_samples | 23 通过、1 项 POSIX 专用检查跳过 |
| test_automatic_failure_sample_admission | 23 通过 |
| test_default_failure_evidence | 4 通过 |
| test_failure_capture_ownership | 13 通过 |
| test_full_client_diagnostic_evidence | 4 通过 |

合计 241 项检查：239 通过、2 项 POSIX 专用检查在 Windows 跳过。`test_full_client_diagnostic_evidence` 使用真实 Windows 写入器和导出器，验证 1080p/1440p/4K 全尺寸及边缘逐像素回读、4K 大于旧 12 MiB 的 PNG 随 ZIP 导出、四上下文默认导出及关闭附图/纯文字选择。三张 4K 高熵图触发真实 64 MiB 字节轮换，只留最新两张完整图片，退休记录仍保留；3500×3400 高熵图超过单 PNG 32 MiB 时明确拒绝、不写图、不裁剪、不消费成功冷却。

## 图片体积、耗时与内存实测

命令：`python tests/measure_full_client_diagnostic_evidence.py`。输入是固定种子的 **BGR 三通道合成图**，包括可压缩的结构示意和高熵压力图，均非真实游戏或常见画面抽样。每格一次测量，不提供百分位或游戏延迟结论；保存仅在新建临时目录。编码使用现行 `cv2.imencode('.png', frame)`，实际写入阶段调用现行自动采样写入器，包含编码、journal、身份与文件 IO。

| 合成输入 | 分辨率 | 原始像素 bytes | PNG bytes | 编码 ms | 实际写入 ms | 写入工作集峰值增量 MiB | 单 PNG ZIP bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 结构示意 | 1920×1080 | 6,220,800 | 105,437 | 7.924 | 153.551 | 1.83 | 7,068 |
| 结构示意 | 2560×1440 | 11,059,200 | 143,710 | 13.620 | 67.993 | 0.07 | 6,254 |
| 结构示意 | 3840×2160 | 24,883,200 | 233,099 | 29.049 | 158.752 | 1.15 | 8,832 |
| 高熵压力 | 1920×1080 | 6,220,800 | 6,221,583 | 35.002 | 305.270 | 19.45 | 6,223,624 |
| 高熵压力 | 2560×1440 | 11,059,200 | 11,065,070 | 56.505 | 405.938 | 31.48 | 11,068,591 |
| 高熵压力 | 3840×2160 | 24,883,200 | 24,906,599 | 127.952 | 1,317.757 | 78.31 | 24,914,317 |

4K 高熵压力 PNG 约 23.75 MiB，超过旧 12 MiB，上调到单张 32 MiB 有实际依据；同上下文三张这样的图超过 64 MiB，应减少保留张数。32 MiB 不能保证任意分辨率可存，超限保留明确缺图原因。

内存值是每 5 ms 采样的进程工作集相对阶段开始的增量，排除已创建的输入像素；同进程顺序测量受已保留内存、系统缓存和采样粒度影响，0 不代表没有分配。4K 高熵编码加输出副本阶段观察到 49,623,040 bytes 增量，ZIP 压缩阶段 50,212,864 bytes；均不与写入阶段相加。ZIP 仅测单 PNG 的内存内压缩容器，4K 高熵压缩 551.143 ms；完整默认诊断包另含脱敏文字/元数据，其流程由 InfoService 回归覆盖，这个 ZIP 大小不是整包上限。实际写入内存测量为新空库存，不代表满 64 MiB 库存时的最坏峰值；重复库存 IO 是后续可优化项。

## 证据含义及未验收项

合成像素不代表游戏画面；离屏 Qt 不代表玩家可见界面通过。原生 Windows 文件创建、身份/hash 回读、同句柄轮换与过期检查是文件安全证据，不是实际游戏采集验收。捕获器/运行时测试使用合成提供者或原生 API 替身，未操作对局、切换前台或启动/停止用户程序。

尚未验证：绑定真实游戏窗口后的失败复现、不同显示缩放/窗口尺寸下的完整边缘、玩家可见帮助页与导出操作、最终整合提交的联合检查、Setup 安装及 huan 本机验收。后台本轮没有复用桌面浏览器或已绑定游戏会话。生命周期已经撤销的帧仍拒绝写入，不能为了补图重新采集或恢复旧凭据。

接通反馈前，必须同时具备默认整帧保存与默认诊断导出、完整像素/来源一致、相关检查通过、Windows 未测项列清的证据；最终整合与真实游戏验收仍待完成，不能据本轮离屏检查自行上线。

## 重叠与整合

当前核实：#133 `06ef751`、#138 `8463cbe`、#141 `17430c3`、#142 `d672177` 均仍为草稿，运行时文件重叠。仅整合本轮诊断函数变化：复用原合法整帧、单次自动写入、不重复手动写入；保留对方运行时/资源新实现，不能整文件覆盖或带入识别变更。

#123 `9d1090b` 与诊断存储/导出及测试重叠：保留原归属、原文件身份/hash、Windows 句柄保护与未知文件失败关闭；将单图 32 MiB、单上下文 64 MiB、一次导出 64 MiB 一起整合，避免写入可存但库存或导出读不到。#108 `5790653` 与四上下文导出、schema、InfoService/测试潜在重叠，保留相同会话匹配和一个共享导出图片预算。

独立 Reviewer 对 7 个生产文件和两份说明复核，未发现阻断性缺陷。非阻塞后续建议：诊断库存可能在同一事务中多次读取验证，未来可减少重复 IO，但须保留最终目标文件的同句柄身份/hash 校验；本轮不借此扩改存储安全。
