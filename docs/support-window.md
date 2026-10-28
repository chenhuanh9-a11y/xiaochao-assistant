# 原生赞赏窗口

赞赏窗口是独立的 Tk 小窗口。只在帮助页底部点击“打开赞赏窗口”时出现，不阻止操作主窗口，不影响免费功能，不启动游戏监听、监测、连点、浏览器或付款。

## 独立预览

在已安装项目依赖的 Python 环境中执行：

```powershell
python -B tools/preview_support_window.py
```

这条入口只导入赞赏窗所需的 UI 模块，不导入游戏助手。以下命令只核对图片可读性，不创建 Tk 窗口：

```powershell
python -B tools/preview_support_window.py --check-assets
python -B -m unittest discover -s tests -p test_support_window.py -v
```

## 素材与行为

- `assets/support/wechat.png`、`assets/support/alipay.png` 是用户授权提供的干净派生 PNG，已去掉外围姓名。原始含身份图片不在仓库。
- 显示时保留整张图片和白边，使用最近邻缩放且不放大源图，不重生成二维码或改变配色。窗口不解析、输出或保存收款载荷。
- 点击微信、支付宝切换；Esc、关闭按钮或窗口关闭均可退出。窗口没有模态抓取，也没有自动弹出逻辑。
- 图片加载和 `PhotoImage` 创建在 Tk 事件线程进行。平台切换和关闭取消待执行任务，关闭释放图片；缺图时提供错误文字和重新加载入口。
- 重复点击帮助页入口复用同一窗口，关闭后可以重新打开。窗口使用现有 `WindowDisplay` 处理 DPI、字体和工作区限制。
- 裁掉外围姓名不代表微信、支付宝扫码后的收款信息匿名；收款平台的展示由平台决定。

交互结构参考 [TipFrame](https://github.com/pseuo/TipFrame) 的方式切换、关闭/Esc 与加载失败反馈；此模块独立实现，没有复制其 HTML、CSS 或 JavaScript。

## 打包交接与证据边界

独立源码预览会从仓库 `assets/support` 读取图片。冻结构建使用 `_MEIPASS/assets/support`；集成负责人采用此分支时需将这两个文件收集到该位置，例如 PyInstaller 参数 `--add-data "assets/support;assets/support"`。本分支没有修改构建脚本或已交付安装包。

当前自动检查覆盖实际模块的加载、切换、失败重试、关闭取消、图片引用释放、入口复用及缩放像素边界。测试替换的是原生窗口/widget、调度和 `PhotoImage` I/O，图片读取和状态方法保留实际实现。

没有运行新 GUI、桌面输入、游戏或支付，也没有生成模拟截图。尚需在获准的真实窗口中核对默认尺寸、150%/200% DPI、较小工作区、键盘焦点和扫码清晰度；自动检查不能代替这些观察。
