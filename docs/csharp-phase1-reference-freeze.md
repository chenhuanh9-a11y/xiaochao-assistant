# 阶段一基线冻结

冻结时点的发布版仍是 Python/Qt。本文件只记录身份，不删除、不修改 `app/` 或 `experiments/qt_quick/` 的产品代码。

| 项 | 值 |
| --- | --- |
| 产品源码 | `origin/main` `7991dbbecdb66d76799415fb555d2c8a0361acc6` |
| 显示版本 | `version.json`：1.1.2 / 数值版本 1.0.0.24 |
| `version.json` SHA-256 | `a3739e9c7b8d7461642d3a88990b128ff8fdd69c6b6858b31094bc838aba2efa` |
| 正式入口 | `tools/qt_client_entry.py`，打包规格 `tools/qt-client.spec`（`uac_admin=True`） |
| Qt 会话 | `experiments/qt_quick/client_session.py` 的 `SessionService` |

重新核对源码身份：

```bash
git rev-parse origin/main
```

阶段一分支出自此提交。之后 `main` 若前进，本表仍是阶段一的对照基线，不自动跟着变。

## 地图与资源包怎么认

程序源码冻结和资源锁不是同一个提交。

- 显示用图包：`docs/resources-q90.lock.json`（SHA-256 `7641e41fadee6e23916fe758abf6b9e4eb93ee1a9f595a0d9e4e61ede0653dce`）。认 `resourceSet`（`display-q90-r1`）、每个 `packageId`、`bytes`、`sha256`。锁内 `sourceCodeBaseline` 是资源配方当时的源码，不要把它写成当前产品提交。
- S1 生产文件：`tools/s1-production-release-lock.json`（SHA-256 `7787f107314fa52ffa039e4fc35832af215cea3c78595cff048d2eabc42fc85a`）。每个相对路径对应 `bytes` 与 `sha256`。

列出锁内身份、且不打印本机路径：

```bash
python3 tools/phase1/identify_resources.py
```

核对一份落在磁盘上的包时只输出字节数和 SHA-256，不输出路径：

```bash
python3 tools/phase1/identify_resources.py --file <包文件>
```

这些 ZIP / 生产素材不在本分支里，也不要为了对等测试把它们提交进来。阶段一的录制帧是 `csharp/fixtures/` 下的合成几何，manifest 标明 `synthetic: true`。它们不能冒充授权游戏帧或上述图包。

以后若使用获授权的真实帧，用帧文件字节的 SHA-256 加上当时对照的资源锁身份一起登记。日志只写帧标识和哈希，不写含用户目录的路径，不写其他窗口标题或账号。
