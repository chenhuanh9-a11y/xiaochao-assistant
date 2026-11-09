# 主入口回放

在原项目的 Python 环境生成独立预期。默认输入用当前注册结构重新绘制，包括全域候选、黑帧、无入口索引楼层、零预算以及独立墙观测支持。每个合成 PNG 的候选搜索只接收像素、模式和楼层。生成图时所用的地图 ID 和缩放不会传给求解器。

```powershell
python reference_oracle.py --repo <源仓库> --resources <原资源根> --output <新oracle目录>
dotnet build-server shutdown
dotnet build
dotnet run --no-build -- --resources <原资源根> --oracle <新oracle目录>/index.json --output <新结果目录>
```

真实帧需为 2026-11-05 当日或之后采集的完整游戏客户区。生成器接受可选的 `--recordings <JSON数组>`。每条记录包含 `name, season, mode, floor, captureRecord, timeField, imageField, kindField`。后三个字段为原始采集记录的 JSON 字段路径数组。JSONL 行号用 `line` 指定，从 1 开始。采集记录内的时间须带时区，原始图像路径相对于记录目录，采集类型为 `live-client-capture`。文件修改时间不能证明采集时间。

生成器先按原采集记录确定原图，再在该图运行原 S0 竖排楼层控件或 S1 带符号楼层列检测。当前楼层与输入一致后才使用该真实帧。C# 入口会回读原采集记录，并对实际解码的 PNG 检查其与布局检测所用字节的一致性。组件裁片缺少全客户区证据。历史横排控件图片的通过记录只保留为历史数值，不能用于证明当前功能。缺少合格真实图时，结果中仅有显式合成案例。

每一例先写入实际 `actual.json`，再读取对应的预期做逐字段、逐数值比较。耗时属于各运行时的实际调度，比较范围只移除 `elapsedMs` 和前门观测时间字段。比对涵盖原始角点、布局预筛、初始与精化假设、全注册库存和裁决。全自动会话提交与实机体验仍需上层验证。
