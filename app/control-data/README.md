# S0 控件模板

`s0-floor-templates-v1.npz` 是程序拥有的固定字体数值资源，不含截图或游戏素材。
客户端的参考库和结构包导出端均读取此文件，不在用户机器重画字体、归一化或生成参考库。
S1 控件仍直接导出已有源码中的固定参数；两者都在导出后走实际客户端准入校验。

本文件由 `tools/generate_s0_floor_templates.py` 显式生成，生成环境为 Python 3.13.3、
NumPy 2.2.6、OpenCV 4.12.0.88（`cv2.__version__ == 4.12.0`，CPU 优化开启）。
720 × 6144 的 float32 vectors 和 int64 labels 与修复前生产环境的 `_bank()` 数值完全一致。
生成实现使用原字体变体和计算顺序，没有更改识别算法。

仅在有意修改模板时，在生产构建环境运行以下命令，输出到新路径，复核后替换版本化资源：

```powershell
python -B tools/generate_s0_floor_templates.py --output <新文件路径>.npz
```

普通导出和安装构建不运行生成器。缺少或损坏该文件时应修复安装，禁止自动重建来掩盖错误。

客户端按源码中的 `BANK_ASSET_SHA256` 读取固定资产；`BANK_ID` 来自生成参数规范 JSON，
不来自浮点渲染值。新的 S0 结构包 manifest 格式为 2，声明 `bankId`、`generatorVersion`；
旧格式 1 结构包保持兼容。两种格式均检查整数标签、形状、数值有效性和逐行余弦
`>= 1 - 1e-6`，不做浮点逐位比较。生成器使用同一 `BANK_GRID`，只在显式开发命令中绘制。
跨构建黄金样本位于 `tests/fixtures/s0-controls-golden/`，不含游戏素材。
