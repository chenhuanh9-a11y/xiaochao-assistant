# 发布 Windows 更新信息

星图检查更新时读取 `https://api.xingchenmap.cn/updates/windows.json`。发布新版后，用 [tools/publish_windows_notice.py](../tools/publish_windows_notice.py) 提交版本号和更新公告。服务器只保存版本信息，安装包放在夸克和 UC 网盘。

## 准备

1. 确认 `version.json` 的 `displayVersion` 就是这次的版本，并用它打出正式安装包 `星图-X.Y.Z-Setup.exe`。
2. 把同一个安装包上传到夸克和 UC 的公开目录。建议上传前改名为 `IdentityV-Map-pc-X.Y.Z.exe`，和以往版本保持一致。脚本两种名字都接受。
3. 把公告写进一个 UTF-8 文本文件，格式固定：

```text
更新公告：
PC端 vX.Y.Z
更新内容：
1. ……
2. ……
夸克网盘链接：https://pan.quark.cn/s/4cec4ca814f7
UC网盘链接：https://drive.uc.cn/s/df8af96910bb4?public=1
```

公告规则：总字数（含链接）不超过 600；只写这一版的变化；不写作者署名；没有的项直接省略。

## 预览（不会发布）

```powershell
python tools/publish_windows_notice.py --installer "D:\发布\星图-1.0.2-Setup.exe" --notes notes.txt
```

预览会读取线上当前版本，把内部版本号自动设为线上值 + 1，并检查以下几项：版本比线上新、公告格式和字数、安装包文件名。最后计算安装包大小和 SHA-256。预览不需要口令，AI 助手可以代为运行。

## 发布

确认预览无误后，**由发布人本人**在自己的终端里运行：

```powershell
python tools/publish_windows_notice.py --installer "D:\发布\星图-1.0.2-Setup.exe" --notes notes.txt --publish
```

- 先输入版本号确认，再在隐藏提示里输入管理员口令。口令不会显示、不会保存。
- 脚本只能在交互终端里发布，AI 助手在后台运行会被拒绝。
- 发布后脚本会重新读取线上信息，逐项核对。

## 注意

- 发出去不能修改或删除。写错了只能用更大的内部版本号重新发一次。
- 提示口令不正确时脚本会停下，不要反复尝试。网络中断、结果不明时，先重新预览看线上状态，不要直接重发。
- 管理员口令和安卓版共用，不要发到聊天、文档或代码里。
- 旧版 1.7.x 不会收到 1.0.x 的提醒，旧版用户要靠公告自己去网盘下载新版。
