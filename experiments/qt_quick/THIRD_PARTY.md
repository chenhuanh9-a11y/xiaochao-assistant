# Qt 实验依赖与素材来源

本实验从官方 PyPI 安装固定版本，没有购买商业许可或使用 Qt 商业安装器。
Python 绑定依赖为 `PySide6-Essentials==6.11.1` / `shiboken6==6.11.1`。
已核对 wheel METADATA 的许可证表达式：`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`。
本实验选择其 LGPL v3 许可路线；Qt 商标仍属于 Qt Group。

使用的模块：QtCore、QtGui、QtQml、QtQuick、QtQuick.Controls.Basic、
QtQuick.Layouts、QtQuick.Dialogs；自动化检查另用 QtTest。
没有使用 Qt Charts、Qt Graphs 或其他仅 GPL / 商业的额外组件。
最终分发必须按实际收集的模块和插件逐项保留许可、第三方声明、对应源码获取信息，
并满足 LGPL 对可替换动态库/重新链接与反向工程调试等要求。当前仅源代码实验，未做二进制分发合规完成声明。

官方依据：

- [Qt for Python 许可证与第三方说明](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt Quick 6.11 许可与归属](https://doc.qt.io/qt-6.11/qtquick-index.html#licenses-and-attributions)
- [Qt Quick Controls 6.11](https://doc.qt.io/qt-6.11/qtquickcontrols-index.html#licenses-and-attributions)
- [Qt 开源 LGPL 义务](https://www.qt.io/licensing/open-source-lgpl-obligations)
- [Qt for Python 源代码](https://code.qt.io/cgit/pyside/pyside-setup.git/)
- [Qt Declarative 源代码](https://code.qt.io/cgit/qt/qtdeclarative.git/)

另外使用本项目已固定的 NumPy 2.2.6（BSD）、Pillow 12.3.0（HPND）、
psutil 7.2.2（BSD）。Qt 图片插件以及这些依赖的各自第三方声明仍应跟随最终分发。
完整客户端原有 OpenCV 等依赖及声明继续适用；本实验未复制这些算法。

所有 QML 布局、控件样式和几何图标为本次编写。无图片生成、无外部图库下载。
地图内容来自用户现有的本地隔离测试资源，作者身份和图层绑定由原有清单给出；
它们没有被加入 Git，也没有被当作实验代码的开源素材。
本地验证截图仅用于用户/协调线程审阅，不能据此公开分发作者图片或游戏解包资源。
