# 本目录在 PC 仓库中的位置说明

上传时间：2026-11-20 CST（Asia/Shanghai）

本目录是 `tests/fixtures/android-route-packages/`，用于把已整理的安卓路线测试材料放到本仓库，供后续封面生成和广场显示适配使用。这是测试/后续适配素材，**不表示当前 PC importer 已经能直接导入这些样本**。

## 范围（请保持真实边界）

- 只有 **2 套**含真实坐标的完整广场包：困难·凉哈皮复刻（29 图、2431 点）、磷光症 S1 噩梦（15 图、60 个 cell 点）。
- **不是 59 套**公开作品路线包。本材料没有那 59 套的离线归档。
- `editor-identity/` 的模板签名/网格尺寸对照**不是坐标包**。
- 另有 1 份单测格式样例 `samples/scoped-route-packages.json`，不能计为广场作品。

详细边界、S0 困难 / S1 编辑器路径、离线解包流程，以及当前 PC importer 不直接兼容这两份 format 4 样本的限制，见同目录 `README.md`。

## 校验

`README.md`、`samples/`、`editor-identity/`、`SHA256SUMS` 以及 ZIP 均保持整理时的字节不变，便于核对：

- 打包 ZIP：`route-coordinate-test-samples-20261119.zip`（84622 字节）
- SHA-256：`52bb2b54e40eee52ae7ed8e452473ea66919e7a895b249be7abd69b9dd87000c`（亦见 `route-coordinate-test-samples-20261119.zip.sha256`）
- 包内文件校验：`SHA256SUMS`

同目录 `README.md` 写于上传前，文中“尚未上传 / 待用户审核后转交”是当时的整理说明，不代表本仓库仍未收录这些文件。

请不要用会增加下载计数的接口去补拉其余作品。本提交不改业务代码、版本号或发布状态。
