"""Read-only provenance audit; never promotes partial evidence to raw closure."""
import argparse
import json
from pathlib import Path
from build_entry_candidates import read, file_hash, within


def audit(config_path, allowed_roots):
    config=read(config_path)
    root=Path(config["resourceRoot"]).resolve()
    allowed=[root]+[Path(p).resolve() for p in allowed_roots]
    files={}
    def inspect(name, expected=None):
        p=Path(name).resolve()
        if not any(p.is_relative_to(r) for r in allowed):
            return dict(path=str(p),status="outside-explicit-audit-roots")
        if str(p) not in files:
            files[str(p)]=dict(path=str(p),status="present" if p.is_file() else "missing",
                              sha256=file_hash(p) if p.is_file() else None)
        result=dict(files[str(p)])
        if expected:
            result["expectedSha256"]=expected
            result["status"]="verified" if result["sha256"]==expected else "hash-mismatch-or-missing"
        return result
    rows=[]
    catalogs={}
    for source in config["sources"]:
        data=read(within(root,source["entries"]))
        for item in data["entries"]:
            row=dict(mode=data["mode"],mapId=item["mapId"],floor=str(item["floor"]),
                     sourceClass=item.get("sourceClass",item.get("sourceKind")),
                     rawClosure="unverified",checks=[],gaps=[])
            if item.get("sourceClass")=="legacy-clean":
                path=within(root,item["structureResource"])
                row["checks"].append(inspect(path,item.get("canvas",{}).get("resourceSHA256")))
                row["gaps"]=["legacy-only-no-authoritative-native-layout-binding","no-raw-native-door-semantics"]
                rows.append(row)
                continue
            name=item.get("sourceGeometry",{}).get("rendererMetadata")
            if not name:
                row["gaps"].append("renderer-metadata-path-missing")
                rows.append(row)
                continue
            check=inspect(name)
            row["checks"].append(check)
            if check["status"]!="present":
                row["gaps"].append("renderer-metadata-unavailable")
                rows.append(row)
                continue
            metadata=read(name)
            render=metadata.get("render",metadata)
            canvas=item.get("canvas",{})
            row["metadataIdentityMatches"]=str(render.get("levelId"))==str(canvas.get("layout")) and render.get("floor")==canvas.get("worldFloor")
            if not row["metadataIdentityMatches"]:
                row["gaps"].append("renderer-metadata-identity-mismatch")
            cat=render.get("sourceCatalog",{})
            if cat.get("sourceFile"):
                check=inspect(cat["sourceFile"],cat.get("sourceFileSha256"))
                row["checks"].append(check)
                if check["status"]=="verified":
                    if cat["sourceFile"] not in catalogs:
                        c=read(cat["sourceFile"])
                        modules=[]
                        for module in c.get("sourceModules",{}).values():
                            if isinstance(module,dict) and module.get("file"):
                                if Path(module["file"]).is_absolute():
                                    modules.append(inspect(module["file"],module.get("sha256")))
                                else:
                                    modules.append(dict(path=module["file"],status="relative-source-base-unresolved"))
                        catalogs[cat["sourceFile"]]=dict(modules=modules,gameConfigVersion=c.get("gameConfigVersion"))
                    row["catalogEvidence"]=catalogs[cat["sourceFile"]]
                else:
                    row["gaps"].append("catalog-hash-not-verified")
            else:
                row["gaps"].append("catalog-binding-missing")
            # Existence/hashes of selected metadata and scripts do not prove all source textures,
            # overlay ordering, extractor versions or their relationship to the generated NPZ.
            row["gaps"].extend(["texture-byte-dependency-closure-unverified","renderer-module-root-and-closure-unverified",
                                "source-to-derived-product-replay-not-performed"])
            rows.append(row)
    return dict(schema="entry-source-audit/1",rows=rows,files=list(files.values()),
                runtimeIntegration="unchanged",rawClosureVerified=0,
                policy="partial evidence only; no regeneration, no native identity inferred from legacy")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",required=True,type=Path)
    p.add_argument("--allow-source-root",action="append",default=[])
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    result=audit(a.config,a.allow_source_root)
    with a.output.open("x",encoding="utf8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(rows=len(result["rows"]),uniqueFiles=len(result["files"]),rawClosureVerified=0,output=str(a.output))))
