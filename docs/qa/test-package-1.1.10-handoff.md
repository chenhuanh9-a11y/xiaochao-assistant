# 星图 1.1.10 测试包交接

本记录归属「安装与更新」。相邻的「排查与帮助」沿用现有验收反馈入口。交接文档以 `main` 的 `d80f9f86fe4a` 为基线。

## 产物身份

- 版本为 `1.1.10`，数值版本为 `1.0.0.32`。两项均与来源提交的 `version.json` 相符。
- 构建来源为 `cf65363dcbe6633da64d051eb377667f71b4249c`。[原版本号 PR #191](https://github.com/chenhuanh9-a11y/xiaochao-assistant/pull/191) 的头提交与收据一致，该 PR 已关闭且未合并。
- 收据标记安装器为 `feature-test` 测试实例。`测试清单.md` 列出的默认安装位置为系统 Program Files 下的 `XingtuFeatureTest`，开始菜单项为 `Xingtu Feature Test`。收据未记录安装位置和 AppId，实际安装位置、AppId 及数据隔离尚未核实。

## 本机只读核对

2026-11-20 核对到以下文件位于同一目录。安装器和测试清单的位置均与收据中的记录一致。

| 文件 | 实测大小 | 核对结果 |
| --- | ---: | --- |
| `XingtuFeatureTest-Setup-1.1.10-feature-test-cf65363dcbe6.exe` | 249,973,194 字节 | 文件存在，大小和 SHA-256 均与收据一致。 |
| `test-receipt.json` | 1,676 字节 | 文件存在且可解析，记录了版本、来源提交、测试实例、安装器大小及 SHA-256。 |
| `测试清单.md` | 12,278 字节 | 文件存在且可读取，清单标注的版本为 1.1.10。 |

用 PowerShell 的 `Get-FileHash -Algorithm SHA256` 实测安装器摘要，并与收据的 `sha256` 字段比对，结果一致。

```text
edb7ef8b2213879ad302756281ce59d2b834cb18cbb80f40d0677d05d6c08bbf
```

## 待验收

收据的 `installed` 和 `gameTested` 均为 `false`。本次只读取文件，Windows 安装与游戏内验收均待完成。通知送达和文件校验只记录各自的结果，实机验收以实际操作结果为准。正式安装流程的用例仍归正式包验收。
