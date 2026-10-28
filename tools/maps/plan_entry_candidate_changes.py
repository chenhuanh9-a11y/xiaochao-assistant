"""Plan candidate cache events without deleting resources or changing runtime."""
import argparse
import json
from build_entry_candidates import read


def plan(old, new, unavailable=()):
    def index(manifest):
        if manifest.get("schema") != "entry-candidates/1":
            raise ValueError("unsupported manifest")
        rows={}
        for row in manifest["entries"]:
            key=tuple(row["identity"])
            if key in rows:
                raise ValueError("duplicate identity")
            rows[key]=row
        return rows
    before,after=index(old),index(new)
    blocked={tuple(x) for x in unavailable}
    if blocked-set(before)-set(after):
        raise ValueError("unknown affected identity")
    events=[]
    for key in sorted(set(before)|set(after)):
        a,b=before.get(key),after.get(key)
        action=("fallback" if key in blocked or b is None else "rebuild" if a is None or a["sha256"]!=b["sha256"] else "reuse")
        events.append(dict(identity=key,action=action,oldSha256=a and a["sha256"],newSha256=b and b["sha256"],
                           invalidateOptionalCache=action!="reuse",deleteOldResource=False))
    return dict(schema="entry-candidate-events/1",events=events,
                policy="fallback never grants identity; rebuild only with verified available inputs")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old",required=True);p.add_argument("--new",required=True)
    p.add_argument("--unavailable",help="JSON list of affected complete identities from a dependency audit")
    p.add_argument("--output",required=True)
    a=p.parse_args()
    result=plan(read(a.old),read(a.new),read(a.unavailable) if a.unavailable else [])
    with open(a.output,"x",encoding="utf8") as f:json.dump(result,f,indent=2)
