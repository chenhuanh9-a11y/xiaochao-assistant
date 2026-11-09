"""Original pure session oracle: lifecycle, incremental evidence, bounds and failure paths."""
import argparse
import copy
import json
from pathlib import Path
import sys

import numpy as np


def plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: plain(val) for key, val in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(val) for val in value]
    return value


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError(a.output)
    sys.path.insert(0, str(a.repo.resolve() / "app"))
    from s1_structure_selection import StructureCatalog, StructureCandidateScanner, StructureEvidenceSession, ObservationTicket, verified_pose
    cat = StructureCatalog("s1", "difficult", "state-generation", ("a", "b", "c"), ("-1", "-2", "-3", "-4"))

    def pose():
        return dict(success=True, reason="shared-fit", matrixSourceToClient=np.eye(3),
            coordinateSpace="shared-structure-to-client", selectedRouteCurrentFrameResidual=dict(
                verified=True, poseVerifiedAgainstSharedStructure=True, withinThreePixels=.95))

    def scan(supported=("a", "b"), value=1, floor="-1", **options):
        return StructureCandidateScanner(cat, lambda f, mid, fl, c, **kw: pose() if mid in supported else dict(success=False, reason="unknown")).scan(
            np.full((8, 8, 3), value, np.uint8), floor=floor, floor_source="operator-declared", **options)

    cases = []

    def case(name, operations, maximum=2):
        session = StructureEvidenceSession(cat, "round-1", max_observations=maximum)
        tickets = {}
        result = []
        for operation in operations:
            op = copy.deepcopy(operation)
            name_op = op["op"]
            try:
                if name_op == "begin":
                    tickets[op["ticket"]] = session.begin_observation(op["floor"])
                    actual = dict(generation=session.generation, floor=op["floor"])
                elif name_op == "commit":
                    actual = session.commit(op["report"], tickets[op["ticket"]])
                elif name_op == "choose":
                    actual = session.choose_candidate(op["mapId"], tickets[op["ticket"]])
                elif name_op == "snapshot":
                    actual = session.snapshot()
                elif name_op == "priority":
                    actual = session.priority_order(op["floor"])
                elif name_op == "withdraw":
                    actual = session.withdraw_material(tickets[op["ticket"]])
                elif name_op == "foreign":
                    actual = session.commit(op["report"], ObservationTicket("other-session", "round-1", session.generation, "-1"))
                elif name_op == "revoke":
                    actual = session.revoke_current()
                elif name_op == "end":
                    actual = session.end_round()
                else:
                    raise ValueError(name_op)
                result.append(dict(operation=op, expected=plain(actual)))
            except ValueError as error:
                result.append(dict(operation=op, expectedError=str(error)))
        cases.append(dict(name=name, maximum=maximum, steps=result))

    begin = lambda token="t", floor="-1": dict(op="begin", ticket=token, floor=floor)
    commit = lambda report, token="t": dict(op="commit", report=report, ticket=token)
    choose = lambda mid="a", token="t": dict(op="choose", mapId=mid, ticket=token)
    snapshot = dict(op="snapshot")
    partials = []
    final = scan(on_progress=partials.append)
    changed = copy.deepcopy(partials[-1]); changed["updateSeq"] += 1; changed["poses"]["a"]["extra"] = "changed"
    case("monotonic-evidence", [begin(), commit(partials[0]), choose(), snapshot, commit(partials[1]),
        commit(partials[0]), commit(changed), commit(final), snapshot, commit(final), choose("c")])
    cancelled = copy.deepcopy(final)
    cancelled.update(status="cancelled", scanComplete=False, supportedMapIds=[], poses={}, unknownMapIds=list(cat.map_ids))
    case("cancel-withdraws-partial", [begin(), commit(partials[0]), commit(cancelled), snapshot, choose(), commit(final), dict(op="priority", floor="-1")])
    case("withdraw-does-not-revive", [begin(), commit(partials[0]), dict(op="withdraw", ticket="t"), snapshot, commit(final), choose(), begin("t2"), commit(final,"t2"), snapshot])
    case("floor-round-context", [begin(), commit(final), begin("t2","-2"), choose(), commit(final,"t2"), dict(op="priority",floor="-2"),
        dict(op="foreign",report=final), dict(op="revoke"), snapshot, dict(op="end"), commit(final), begin("t3")])
    case("bounded-independent-observations", [begin(),commit(scan(("a","b"),value=1)),begin("t2"),commit(scan(("b",),value=2),"t2"),
        begin("t3","-2"),commit(scan(("c",),value=3,floor="-2"),"t3"),snapshot,dict(op="priority",floor="-1"),dict(op="priority",floor="-2")])
    case("same-frame-is-one-observation", [begin(),commit(scan(("a",))),begin("t2"),commit(scan(("b",)),"t2"),snapshot,dict(op="priority",floor="-1")])
    case("unknown-does-not-negate-hints", [begin(),commit(final),begin("t2"),commit(scan((),value=2),"t2"),snapshot,dict(op="priority",floor="-1")])
    preferred = scan(preferred_map_id="b", preferred_material_committed=lambda mid: True)
    first = scan(("b",), first_material_committed=lambda mid: True)
    subset = scan(planned_map_ids=("a",))
    case("material-and-subset", [begin(),commit(preferred),snapshot,choose("b"),begin("t2"),commit(first,"t2"),snapshot,begin("t3"),commit(subset,"t3"),snapshot])
    legacy = copy.deepcopy(final); legacy.pop("updateSeq")
    case("legacy-one-shot", [begin(),commit(legacy),snapshot,choose()])
    malformed = []
    for key, value in (("resourceKey","other"),("expectedMapIds",["a"]),("competitionComplete",True),("unknownMapIds",[]),
                       ("observedFloor","-1"),("frameKey","short"),("updateSeq",True),("scanComplete",False)):
        report = copy.deepcopy(final); report[key] = value
        malformed.append(commit(report))
    case("invalid-reports-do-not-commit", [begin(),*malformed,commit(final),snapshot])
    pose_cases = []
    for name, mutate in (("valid",lambda fit: None), ("string-matrix",lambda fit: fit.update(matrixSourceToClient=np.eye(3).astype(str))),
        ("boolean-matrix",lambda fit: fit.update(matrixSourceToClient=np.eye(3).astype(bool))),
        ("numeric-success-is-not-true",lambda fit: fit.update(success=1)),
        ("wrong-coordinate-space",lambda fit: fit.update(coordinateSpace="author-render-to-client")),
        ("unverified-shared",lambda fit: fit["selectedRouteCurrentFrameResidual"].update(poseVerifiedAgainstSharedStructure=False)),
        ("negative-scale",lambda fit: fit.update(matrixSourceToClient=np.diag([-1.,-1.,1.]))),
        ("nonuniform-scale",lambda fit: fit.update(matrixSourceToClient=np.diag([1.,2.,1.]))),
        ("projective-row",lambda fit: fit.update(matrixSourceToClient=[[1,0,0],[0,1,0],[.01,0,1]]))):
        fit=pose();mutate(fit);pose_cases.append(dict(name=name,fit=plain(fit),expected=verified_pose(fit)))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(plain(dict(schemaVersion=1,cases=cases,poseCases=pose_cases)),ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(cases=len(cases),steps=sum(len(c["steps"]) for c in cases),output=str(a.output))))


if __name__ == "__main__":
    main()
