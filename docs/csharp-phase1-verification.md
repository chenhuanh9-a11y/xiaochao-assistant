# 阶段一验证范围

## 这次在 Linux 上实际跑过的结果

环境：Python 3.12.3、NumPy 2.2.6、.NET SDK 8.0.427。合成帧，不是游戏画面。

`StarMap.Core.Tests` 全部断言通过（序号、代际、迟到与重复、超时先失效再取消、关闭、退避、握手拒绝、热键不消费、黑帧、取消返回空列表、失败信息不含路径）。

`compare_grid_slice.py` 四处结构 SHA-256 一致，无容差：

| 输入 | 结构 SHA-256 |
| --- | --- |
| black-frame | `bb9934308e8981c7c0d627cd4a7f967d5f2ea2ab766f8a78a85c529045816af2` |
| prior-local | `099fc7686f70689b8e5d4e8cb87e72270061dace667552b93138b06bfd57b68f` |
| global-large | `b75809a0854fe74b61d3496086f80ddd84a296c6c824a95e3af8679d439427bf` |
| prior-local --cancel | `7fe90b4540504a0cbbf69933487b69f1e870deb4194a5f0f49f1624029f99827` |

`global-large` 这一次：门限点数 300、进入 `_grid` 的粗点 96、8 行候选，`computeMs` 15.02。这是单次计算耗时，不是 p50/p95，也不是到可见像素的端到端。端到端、未显示次数、有真值的错层：未知。

`dotnet build csharp/src/StarMap.App/StarMap.App.csproj` 在本机停在 `NETSDK1100`。没有打开 `EnableWindowsTargeting`，因此没有在 Linux 上假装编过 WPF。

`measure_publish.py` 在没有发布目录时打印 `coldStartMs unknown` 和体积 `unknown`。

## Linux 上要跑的命令

```bash
dotnet build csharp/StarMap.Linux.sln -c Release
dotnet run --project csharp/tests/StarMap.Core.Tests/StarMap.Core.Tests.csproj -c Release --no-build
python3 tools/phase1/compare_grid_slice.py
python3 tools/phase1/measure_publish.py
```

`compare_grid_slice.py` 对 `black-frame`、`prior-local`、`global-large` 以及 `prior-local --cancel` 做严格哈希比较。输入是合成帧。NumPy 固定 2.2.6。计算耗时由命令行 JSON 的 `computeMs` 给出，那是单次切片耗时，不是端到端可见像素，也没有 p50/p95 样本组。

类库测试覆盖：序号单调与代际、迟到/重复/会话不符、超时先失效再取消、关闭后拒绝新请求、重启退避上限与关闭后不再重启、协议版本和能力拒绝、热键不消费、黑帧 `fast-insufficient-edges`、加载失败的异常不含路径。

## 不能写成已经通过的部分

- WPF 工程在本 Linux 环境没有构建，也没有打开窗口。
- 自包含 publish 的字节数、安装后体积、冷启动：未知。
- 没有 Windows 低级键盘钩子的实测，不能宣称系统键穿透已在实机确认。政策只在代码和单元测试里。
- 没有授权游戏帧、没有真实 GUI、没有实机识别。合成帧通过不等于地图识别通过。
- 错层次数：无图像真值，记未知。未显示次数：本切片不提交显示，记未知。
- 现版 Python 识别逻辑、阈值和热键没有被修改，也没有在这里重跑全量 Qt 测试。

Windows 验收至少包括：`dotnet build csharp/StarMap.sln -c Release`、上面的 publish 命令、同一合成帧的哈希再跑一遍，以及窗口能加载一帧并显示会话序号与结构哈希。这些做完仍不是游戏验收。
