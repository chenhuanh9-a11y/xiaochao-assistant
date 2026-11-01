# 星图安装器语言来源

`qt-client.iss` 仅声明 `chinesesimp`，直接使用已固定 Inno Setup 6.7.3 编译器附带的 `compiler:Languages\ChineseSimplified.isl`。`ShowLanguageDialog=no` 和 `UsePreviousLanguage=no` 使首次安装、覆盖升级及新生成的卸载程序使用简体中文，不继承旧英文私测包的语言。产品 AppId、安装关系、资源保护、程序 payload 和权限策略均未修改。

## 来源与可复现输入

- 官方翻译目录：[Inno Setup Translations](https://jrsoftware.org/files/istrans/)，将 Chinese (Simplified) 列在 Official translations。
- 官方链接指向的语言源文件：[jrsoftware/issrc ChineseSimplified.isl](https://raw.githubusercontent.com/jrsoftware/issrc/refs/heads/main/Files/Languages/ChineseSimplified.isl)。上游 `main` 是来源定位，不是本次字节锁；本次实际输入以下列本地文件和 SHA256 为准。
- 已有文件：`E:/codex/tools/inno-setup-6.7.3/compiler/Languages/ChineseSimplified.isl`。
- 实际字节数：21,516；SHA256：`e0b0b350e2245f3c5e65586dfe43d574f6e7f06f2261149aba284954b3fc9a8d`。
- 文件头版本：Inno Setup 6.5.0+；维护者：Zhenghan Yang (Kira)。原维护者说明及 TranslatorNote 直接来自官方文件，未更改或冒称自译。未下载或手工制作替代翻译。

只读对照该编译器 `Default.isl`：281 个 `[Messages]` 键均有中文文件对应项，没有缺失键，也没有英文原值非空但中文缺值的项。覆盖标题、按钮、目标目录、标准许可页面标签、安装完成、升级冲突及卸载提示。脚本原有中文产品提示仍保留。此变更不新增产品许可协议，不翻译或改写第三方许可正文。

## 许可与归属

编译器自带 `license.txt` 与 [官方 Inno Setup License](https://jrsoftware.org/files/is/license.txt) 一致：允许用于任意目的（包括商业用途）及修改、再分发；源码再分发须保留已有版权与条件，二进制须保留已有版权与网站地址，不得歪曲来源，修改版须明确标识。版权为 Jordan Russell（1997–2026），部分为 Martijn Laan（2000–2026）。本项目引用编译器原语言文件，保留其维护者说明、内置“关于”及翻译署名，不将翻译文件复制为自有作品；未购买商业许可或接受额外条款。

语言选项依据：[Languages](https://jrsoftware.org/ishelp/topic_languagessection.htm)、[ShowLanguageDialog](https://jrsoftware.org/ishelp/topic_setup_showlanguagedialog.htm)、[UsePreviousLanguage](https://jrsoftware.org/ishelp/topic_setup_usepreviouslanguage.htm)。

本次仅完成语言源码和只读键覆盖核对；未启动编译器、安装器或卸载程序。最终 installer-only 重编须记录语言文件实际字节/哈希、新安装器源码提交、被复用的 `45d28c69` runtime payload 身份，随后单独验证中文向导。源码检查不代表已完成原生安装/升级/卸载验收。
# Installer-only rebuild

`rebuild_qt_installer.py` accepts only committed installer/language/build-tool
changes since the prior clean production record. Any runtime-source change
rejects reuse. It copies the prior exact file projection to a new owned stage,
excluding every work/config/test file, verifies hashes and PE permissions,
rebinds the source identity sidecars, then invokes only ISCC. The record keeps
the original runtime commit, per-file reuse hashes and rebound identity hashes.
It never launches Setup or writes an installed application/registry.
