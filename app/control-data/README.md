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
