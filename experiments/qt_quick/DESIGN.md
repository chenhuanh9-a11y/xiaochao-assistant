---
name: 星图 Qt Quick 实验
description: 沿用星图身份的 Windows 原生地图资源浏览与悬浮预览实验
colors:
  action-blue: "#2f66eb"
  action-pressed: "#1f50cc"
  selected-blue: "#dce8ff"
  hover-blue: "#e7effc"
  canvas: "#f3f6fc"
  sidebar: "#eaf1fd"
  surface: "#ffffff"
  button-surface: "#f1f5fc"
  thumbnail-surface: "#edf2fa"
  heading-ink: "#142b4b"
  brand-ink: "#183a73"
  body-ink: "#354f75"
  button-ink: "#354963"
  muted-ink: "#526785"
  border: "#d6e0ef"
  slider-track: "#cad8ee"
  checkbox-border: "#8da4c4"
  disabled-surface: "#edf1f6"
  disabled-ink: "#8491a7"
  success: "#17775a"
  error: "#b8364a"
typography:
  body:
    fontFamily: "Microsoft YaHei UI"
    fontSize: "13px"
  label:
    fontFamily: "Microsoft YaHei UI"
    fontSize: "12px"
  caption:
    fontFamily: "Microsoft YaHei UI"
    fontSize: "11px"
rounded:
  control: "8px"
  card: "12px"
spacing:
  compact: "8px"
  control: "12px"
  panel: "18px"
  section: "20px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    height: "38px"
    padding: "12px"
  button-default:
    backgroundColor: "{colors.button-surface}"
    textColor: "{colors.button-ink}"
    rounded: "{rounded.control}"
    height: "38px"
    padding: "12px"
  button-selected:
    backgroundColor: "{colors.selected-blue}"
    textColor: "{colors.action-pressed}"
    rounded: "{rounded.control}"
  button-disabled:
    backgroundColor: "{colors.disabled-surface}"
    textColor: "{colors.disabled-ink}"
    rounded: "{rounded.control}"
  resource-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.heading-ink}"
    rounded: "{rounded.card}"
---

# Design System: 星图 Qt Quick 实验

## Overview

保留星图既有产品身份与用户指定的白色、浅蓝色和舒适侧栏。视觉服务于 Windows 桌面的连续操作：筛选本地图层、选择资源、核对预览，再按需导入或打开悬浮窗。这里记录已经实现的原生 Qt 系统，不建立新品牌。

范围仅为此目录的隔离 PySide6 / QML 实验。实现依据为 `Main.qml`、`AppButton.qml`、`AppSlider.qml`、`Overlay.qml`；产品事实与素材边界见 [README.md](README.md) 和 [THIRD_PARTY.md](THIRD_PARTY.md)。本文件只规范该实验，不替代仓库全局规则。尺寸均为 QML 逻辑像素；前置令牌是现有字面量的文档映射，代码尚未集中为主题对象。

独立复审结论为 **ship（隔离实验范围）**：两项状态问题已修复，最终交互检查 16 项通过、QML 警告为零。该结论不代表正式客户端、安装器或完整迁移已经完成。

## Colors

浅蓝与白色提供大面积安静底色，深蓝文字承担层级，明蓝标记动作、选中与焦点。令牌取值见前置区；不引入第二套装饰性强调色。

- **Primary：** `action-blue` 用于主操作、焦点边框、滑条已填充段和忙碌状态点；`action-pressed` 用于主按钮按下态及已选按钮文字；`selected-blue` 与 `hover-blue` 分别承载选中与悬停。
- **Neutral：** `canvas` 为主背景，`sidebar` 为固定导航区，`surface` 为资源卡片、检查面板与输入框。`thumbnail-surface` 将地图图像与白色卡片分层；`button-surface` 承载普通操作。
- **Text：** `heading-ink` 用于资源标题，`brand-ink` 用于品牌及悬浮窗标题，`body-ink` / `button-ink` 用于控件正文，`muted-ink` 用于提示与图层元数据。
- **State：** `disabled-surface` / `disabled-ink` 同时弱化不可用控件；`success` 与 `error` 用于底部状态反馈。状态同时给出文字，不单靠颜色传达。

**The 蓝色反馈 Rule.** 选中、可操作与键盘焦点沿用同一蓝色家族，并用填色、描边和文字区分用途。

## Typography

主窗口与按钮使用 Windows 中文界面字体 `Microsoft YaHei UI`，未加载外部字体。默认正文及按钮为 `body`；辅助说明采用 `label`，卡片楼层、路线及悬浮提示采用 `caption`。

页面标题为粗体（26px），品牌名为粗体（25px），右侧所选图层标题为粗体（17px），悬浮窗栏标题为粗体（14px）。卡片名称加粗，元数据降级为小号、低强调文字；字号服务于桌面操作密度，避免将小号元数据扩展为主要操作文字。

长名称与单行状态信息使用尾部省略；空结果说明及覆盖确认内容允许换行。空结果说明行高为默认字号的 1.5 倍。没有人为指定全局行高或字距。

## Layout

- 主窗口默认（1180 × 790），最小（920 × 640）。左侧导航固定宽（184px），内部留白（20px）；右侧工作区四周留白（28px），纵向区块间距（18px）。
- 顶部为标题和刷新、导入操作；下方依次为模式与搜索、路线与楼层。搜索框首选宽（230px），路线选择框首选宽（168px）。
- 内容区由可伸缩资源网格和固定首选宽（252px）的所选图层面板构成，间距（20px）。检查面板内边距（18px）、纵向间距（14px）。
- 网格列数为 `max(2, floor(width / 240))`，列宽均分可用空间；单元高（206px），卡片右侧及底部各留（12px）。缩略图区域高（132px），保持地图比例。窄窗口保留侧栏与检查面板，由网格承担压缩；已核对宽度 1180 与 940 的本地截图，没有移动端布局约定。
- 底部状态行保持在主工作区内；资源网格独立滚动并在边界停止，滚动条按需出现。不同视图按上下文保存滚动位置，恢复时钳制到有效范围。

## Elevation & Depth

主界面没有自定义阴影、模糊或材质滤镜；层次由浅蓝画布、白色面板、缩略图底色与留白表达。选中资源卡片和键盘焦点使用描边，不通过抬升或缩放表达。

悬浮预览采用透明原生工具窗口、无边框、置顶且不接受激活焦点。面板使用 `Qt.rgba(0.94, 0.97, 1, controlAlpha)`，标题栏使用 `Qt.rgba(0.87, 0.92, 1, controlAlpha)`；这两项是现有 QML 的原始颜色表达。面板透明度和地图图像透明度独立，透明、穿透与不激活分别处理。

## Shapes

普通按钮、搜索和路线输入沿用 `control` 圆角；卡片及检查面板沿用 `card` 圆角。缩略图底板圆角（7px），悬浮面板圆角（16px），复选框指示器圆角（4px）。这些形状保持轻缓，避免抢占地图轮廓。

输入框默认细描边，焦点改用动作蓝。按钮焦点、卡片选中或焦点为（2px）描边。滑条轨道高（4px）、圆角（2px），手柄直径（16px）；焦点时手柄边框由（1px）加至（2px）。

## Components

- **按钮与导航：** `AppButton` 默认高（38px），宽度至少（74px）并适应文字。主按钮按下时加深；普通未选按钮悬停时换浅蓝底；已选按钮保持选中底色。不可用态优先于其他样式。侧栏当前项、模式与楼层筛选共享同一选中语言。
- **搜索与路线：** 搜索支持鼠标选择文本，编辑后（140ms）防抖再查询；路线框保留 Qt 下拉交互，三角标记由 QML Canvas 几何绘制。模式、路线、楼层的显示选择绑定后端当前上下文。
- **资源卡片：** 图片按原比例完整适配，不裁切地图；标题和楼层、作者路线信息分层显示。未安装图包、加载中、读取失败分别给出文字。空列表区分正在读取与没有匹配结果。
- **选择与检查面板：** 选中以稳定图层 ID 协调，预览同步显示精确图层和楼层；未选图片时禁用“在悬浮窗查看”。刷新沿用选择与滚动状态；旧异步任务的结果不能覆盖新上下文。
- **导入与状态：** 导入中禁用导入按钮并更新文案；读取忙碌时禁用刷新。文件选择确认后进入校验、导入、刷新，只有需要覆盖时才弹出所属窗口的模态确认。成功、忙碌、错误由状态点和状态文字共同表达。
- **悬浮预览：** 宽（350px），展开高（430px）、收起高（66px）。拖动标题栏移动，展开/收起与关闭使用文本按钮；高度变化采用（180ms）`Easing.OutCubic`。控件默认透明度（0.96），范围（0.35–1）；地图默认透明度（0.86），范围（0.1–1）。收起后隐藏正文；关闭后重显保留穿透状态。界面明确标注“浏览预览”“未连接识别”。
- **键盘与可访问性：** 主窗口控件显示焦点样式；资源卡片可 Tab 聚焦、回车选择，并声明 `Accessible.Button`、含名称和楼层的可访问名称及 press action。穿透开关绑定后端，可在主窗口关闭穿透；悬浮窗不接受焦点的契约独立保留。现有实现未提供减弱动效开关，未据此声称已完成全面辅助技术验证。

**已实现的结果回放：** `Main.qml` 在资源页与回放页之间切换并保留页面实例。回放主面板采用深蓝底（`#142035`）；`ReplayCanvas.qml` 将客户端帧等比缩放、居中留边，并将精确记录的路线矩阵应用到路线图像，透明度滑条独立控制叠加层（默认 0.55，范围 0–1）。悬浮窗可通过 `replayMode` 显示同一回放。输入明确标为合成的已保存样本，消费历史生产算法输出；不代表实时识别或显示授权已接通。`prepare_replay.py` 校验输入 SHA、精确路线配置/地图/楼层资源及渲染尺寸，数据保留在被忽略的本地 `work/`。本次回放检查 10 项通过、QML 警告为零；本地 `work/qt-evidence/replay-render/` 中 1180、940 宽度与悬浮窗截图的对比确认了实际像素及矩阵映射，图片不纳入源码。

性能架构沿用 Python 资源模型、后台读取和 Qt 场景图；`GridView` 复用代理、缓存范围（210px），稳定 ID 增量协调，未变化的刷新不重建模型。图像异步解码并限定 `sourceSize`：缩略图（440 × 250）、检查面板（620 × 620）、悬浮预览（640 × 640）。正常界面没有持续装饰动画；底部循环测试条仅由 `probeAnimating` 开启，用于局部测量，不是产品进度反馈。

本机短流程测量只能说明该实验的事件循环、帧信号间隔及进程 RSS；完整数据见 README。没有显示器呈现时间证据，不把 `frameSwapped` 间隔换算为 GPU 或真实 FPS，也不推断完全无卡顿、游戏内性能、长时内存表现或正式安装包大小。当前没有实机识别、游戏窗口或全局快捷键验证。

## Do's and Don'ts

- **Do** 保留白色、浅蓝侧栏、舒适留白及明确的蓝色操作反馈；扩展界面时优先复用现有 QML 控件。
- **Do** 保持地图原比例与作者、图层绑定；界面始终区分浏览预览、加载、缺失、错误和已选状态。
- **Do** 将素材来源与界面实现区分：QML 布局、样式和几何图标为本次编写；地图来自用户已有、获准使用的本地隔离资源，作者身份沿用原清单。没有生成图片或下载外部图库。
- **Don't** 将本地作者地图、游戏资源或包含它们的验证截图加入 Git 或公开分发。复审截图仅存于忽略的 `work/qt-evidence/review-fixes/`，本文件不嵌入图片。
- **Don't** 将浏览缩略图或应用内 QtTest 键盘事件描述为识别正确、全局快捷键可用或正式客户端可替换的证据。
- **Don't** 将测量探针、一次窗口尺寸或未验证能力固化为未来产品的视觉规则；后续分发继续遵守 THIRD_PARTY 中的依赖与素材边界。
