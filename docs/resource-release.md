# 干净结构资源发布

按当前公开范围，仅提供与preview09-19 / 原候选 `0B721D65` 对应的干净结构与识别预制数据。素材Release不是软件正式版或安装包，也不改变程序默认内置两套包策略。

附件组成：132层结构（噩梦72、困难60）、132份预制，以及一份索引，共265个ZIP成员。没有作者路线图、四套地图包、作者RGB贴图、游戏渲染纹理、UI图像、原始游戏包、Logo或软件二进制。

## 下载与核验

下载入口：[干净地图结构与识别预制 Release](https://github.com/chenhuanh9-a11y/xiaochao-assistant/releases/tag/resources-0B721D65-20261027)。该页仅为素材预发布，不是软件正式版。所需文件：

- `xingtu-clean-structures-0B721D65-public1.zip`：53,091,357字节，SHA-256 `4c0a0016d349d4a3a7f508f244051c93517231db0550c80d28317b99680548f0`。
- `STRUCTURE-MANIFEST-0B721D65.json`：逐文件清单和来源说明。
- `STRUCTURE-NOTICES-0B721D65.md`：范围、署名与验证边界。
- `SHA256SUMS-0B721D65.txt`：上述三项的完整校验和。

将附件下载到独立目录，从仓库根目录运行：

```powershell
python tools/maps/assemble_structure_resources.py --assets-dir DOWNLOADS --output-root resources-private/structures-0B721D65
```

工具核对ZIP及265个成员的大小/哈希，拒绝覆盖不一致文件；同目录重跑时复用匹配文件。它不修改应用代码或用户设置。此目录是结构交换数据，不是完整runtime资源根，不能当作整包覆盖当前安装。

## 读取结构

索引为 `structure-index.json`，每项记录mode、mapId、floor、shape、resourcePath、preparedPath及实际结构metadata。结构NPZ只含二值clean/validation、已有known/unknown、墙段及metadata；预制含distance/chains/rays/pitch/shape及绑定metadata。

安装仓库依赖后，可用现有loader读取：

```python
from dataclasses import replace
from pathlib import Path
import json, sys
sys.path.insert(0, "app")
from shared_structure_reference import load_shared_structure
from fast_structure_alignment import load_prepared

root = Path("resources-private/structures-0B721D65")
index = json.loads((root / "structure-index.json").read_text("utf-8"))
row = index["entries"][0]
reference = load_shared_structure(root / row["resourcePath"])
prepared = load_prepared(replace(reference, resource_root=root))
```

## 对应关系与边界

公开结构通过同版源码的真实provider物化，保留clean/validation、known/unknown、墙段与预制各数值；角色字段和配套metadata标准化为独立可读结构格式。本机出处路径改为不暴露目录的追溯标识。图片/作者路线不在本次范围；当前应用的完整资源根还需要其他索引和显示资产，不能凭此交换包宣称克隆即完整运行。

132层结构与132预制经过loader回读及原加载数值逐项相等检查，ZIP成员、哈希、内容与路径扫描通过，组装和重复组装通过。未运行GUI、rank或solver。困难map29两层保留legacy-clean工作稿状态；二值alpha footprint不能冒称完整语义墙/可走区域。03/10身份歧义及真实游戏/玩家验收仍未解决或未完成。

游戏来源结构归原权利人；map29旧画布结构保留展十来源类别，不为第三方素材新增MIT等许可。详见 [署名说明](../resources/0B721D65-public1/NOTICES.md) 与 [资源清单](../resources/0B721D65-public1/manifest.json)。
