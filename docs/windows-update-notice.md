# 发布 Windows 更新信息

星图检查更新时读取 `https://api.xingchenmap.cn/updates/windows.json`。发布新版后，用 [tools/publish_windows_notice.py](../tools/publish_windows_notice.py) 提交版本号和更新公告。服务器只保存版本信息，安装包放在夸克和 UC 网盘。

## 准备

1. 确认 `version.json` 的 `displayVersion` 和 `numericVersion`，从确定的远端 commit 干净构建正式安装包 `星图-X.Y.Z-Setup.exe`。旧包不能仅改名冒充新版本。缺真实安装包时等待下一次受控构建，不为公告配置额外打包。
2. 由构建负责人提供安装包记录，格式见 [windows-build-record.example.json](windows-build-record.example.json)：记录实际源码 commit、显示/数字版本、真实文件名、字节数和 SHA256。记录是可信构建的证据，不是数字签名；不能给旧包手写一份“新版记录”。记录放在忽略的 `release-local/` 目录，不保存口令。
3. 复制 [windows-notes.template.txt](windows-notes.template.txt) 到忽略的根目录 `notes.txt`，替换占位项并核实仅包含相对上一发布版本的已完成变化。格式固定：

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

先预览并给用户核对版本、内部版本号、包路径/大小/哈希和公告全文。用户手动把**同一安装包**上传到夸克与 UC；建议上传时使用 `IdentityV-Map-pc-X.Y.Z.exe`。收到用户明确回复“已上传”之前，不能执行发布，也不能替用户操作网盘或安排自动发布。

## 一次性设置口令（口令持有人做一次）

本节完全由口令持有人自行操作。非敏感配置和预览不需要读取、检查、打印或设置管理员口令，助手不应使用聊天中提供的口令。

在要发布的那台电脑上打开 PowerShell，运行下面这行，按提示输入管理员口令：

```powershell
[Environment]::SetEnvironmentVariable("UPDATE_ADMIN_TOKEN", (Read-Host "管理员口令"), "User")
```

设置后重启终端和 AI 助手，让新的环境变量生效。口令只保存在这台电脑当前用户的环境变量里，脚本不会打印它。以后要换口令，重新运行这一行即可；要取消，把最后的参数换成 `$null`：`[Environment]::SetEnvironmentVariable("UPDATE_ADMIN_TOKEN", $null, "User")`。

## 预览（不会发布）

推荐使用只支持预览的入口。它先核对构建记录和真实安装包的版本绑定、大小及 SHA256，再调用发布脚本的默认 GET-only 预览；入口不接受 `--publish`、`--yes` 或口令参数：

```powershell
python tools/preview_windows_notice.py --installer "D:\发布\星图-1.0.1-Setup.exe" --build-record release-local/build-record.json --notes notes.txt
```

路径只是示例，1.0.1 真实包尚未准备时不能用 1.0 包或测试 fixture 对线上做预览。fixture 测试只使用本地替身。预览不会读取 `UPDATE_ADMIN_TOKEN`；加 `--yes` 本身也不会让原始脚本发布。

原始入口仍保留，要求维护者先完成同样的构建匹配核查：

```powershell
python tools/publish_windows_notice.py --installer "D:\发布\星图-1.0.2-Setup.exe" --notes notes.txt
```

预览会读取线上当前版本，把公告内部版本号 `versionCode` 自动设为线上值 + 1，并检查以下几项：版本比线上新、公告格式和字数、安装包文件名。最后计算安装包大小和 SHA-256。公告内部版本号与程序 `numericVersion` 是不同字段，不能混用。没有真实包时，线上值和本次 `versionCode` 均保持待定，不猜测。

## 发布

安装包已经上传夸克和 UC 之后：

```powershell
python tools/publish_windows_notice.py --installer "D:\发布\星图-1.0.2-Setup.exe" --notes notes.txt --publish --yes
```

`--yes` 表示不再询问确认，口令从 `UPDATE_ADMIN_TOKEN` 读取，适合 AI 助手直接运行。不加 `--yes` 时，会要求在终端里输入版本号确认，并在隐藏提示里输入口令。发布后脚本会重新读取线上信息，逐项核对。

## 注意

- 发出去不能修改或删除。同一版本、同一内容再次运行是幂等核对，不重复发布；同一显示版本修改公告或安装包，即使增大内部版本号也会被拒绝。需要修正时必须准备更高的显示版本和更大的公告内部版本号。客户端按显示版本判断更新，只增内部版本号不会让同版本客户端收到升级提醒。
- 提示口令不正确时脚本会停下，不要反复尝试。网络中断、结果不明时，先重新预览看线上状态，不要直接重发。
- 管理员口令和安卓版共用，不要发到聊天、文档或代码里，也不要让 AI 打印环境变量。
- 旧版 1.7.x 不会收到 1.0.x 的提醒，旧版用户要靠公告自己去网盘下载新版。
- 默认仅预览，`--publish` 才会写服务器，`--yes` 只取消发布过程的交互。两者都不能替代用户安排发布及确认已上传的工作流程。
- 预览读失败会停止；发布请求后如无法回读或内容不一致，结果不明时不会自动再次 POST。先用真实包重新预览核对线上状态，不直接重发；401 也不能反复尝试。
