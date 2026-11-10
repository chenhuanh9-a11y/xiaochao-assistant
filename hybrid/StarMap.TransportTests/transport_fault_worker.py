"""Small test-only JSONL worker for transport write and output fault probes."""
import json
import os
import sys
import time


mode = os.environ.get("HYBRID_TRANSPORT_FAULT_MODE")
print(json.dumps({"ready": True, "evidenceKind": "recorded"}), flush=True)

if mode in {"writeblock-deadline", "writeblock-cancel"}:
    # Finish the ordinary handshake before the test starts its competing timers.
    request = json.loads(sys.stdin.buffer.readline())
    print(json.dumps({"event": "result", "session": request["session"], "id": request["id"],
                      "ok": True, "result": {"contexts": [], "python": "fault-fixture"}}), flush=True)
    time.sleep(60)
elif mode == "bad-output":
    sys.stdin.buffer.readline()
    print("{bad-json", flush=True)
    for _ in range(10):
        print(json.dumps({"event": "started"}), flush=True)
elif mode in {"bad-output-array", "bad-output-missing-event"}:
    request = json.loads(sys.stdin.buffer.readline())
    if mode == "bad-output-array":
        print("[]", flush=True)
    else:
        print(json.dumps({"session": request["session"], "id": request["id"],
                          "ok": True, "result": {}}), flush=True)
    for _ in range(10):
        print(json.dumps({"event": "started"}), flush=True)
elif mode in {"bad-id-string", "bad-id-object", "bad-id-null"}:
    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        if request["id"] == 1:
            bad_id = {"bad-id-string": str(request["id"]), "bad-id-object": {}, "bad-id-null": None}[mode]
            print(json.dumps({"event": "result", "session": request["session"], "id": bad_id,
                              "ok": True, "result": {}}), flush=True)
        else:
            print(json.dumps({"event": "result", "session": request["session"], "id": request["id"],
                              "ok": True, "result": {"operation": request["operation"]}}), flush=True)
elif mode == "timeout-drain":
    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        if request.get("operation") == "cancel":
            response = {"event": "cancel-ack", "session": request["session"], "id": request["id"], "matched": True}
        elif request.get("operation") == "slow":
            time.sleep(0.3)
            response = {"event": "result", "session": request["session"], "id": request["id"],
                        "ok": True, "result": {"drained": True}}
        elif request.get("operation") == "reject":
            response = {"event": "result", "session": request["session"], "id": request["id"],
                        "ok": False, "error": {"code": "expected-rejection", "message": "expected"}}
        else:
            response = {"event": "result", "session": request["session"], "id": request["id"],
                        "ok": True, "result": {"operation": request.get("operation")}}
        print(json.dumps(response), flush=True)
