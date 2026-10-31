# Mouse-up observation and presentation

After a valid drag, the coordinator publishes the updated transform and queues
`probe_map_after_drag`. Session emits its busy state before starting that worker.
The busy-state handler used to clear the aligned window at this point, although
the current runtime grant was still valid, then publish it again on completion.

Treat this observation-only command like `refresh_map` in the busy-state retain
list. Runtime authorization, focus loss, explicit close/end and the existing
two-negative-observation policy still own withdrawal. No timeout, image detector
threshold or pose validation rule changes.

`test_client_drag_probe_presentation.py` exercises the real source mailbox and
queued input through down/move/up, coordinator, Session worker and final
alignedData/window calls in both modes. It checks continuity while the worker is
queued and after completion, plus negative observations, foreground loss, and
successful results delivered late after close/end. Hook installation, OS capture
pixels, solver measurements, DIP projection and window/image sinks are fixtures.

Run from the repository root with `QT_QPA_PLATFORM=offscreen`:

```text
python -B -m unittest discover -s experiments/qt_quick -p test_client_drag_probe_presentation.py -v
```

These are offline production-path regressions. They do not establish real game
acceptance or resolve the separate intermittent drag/geometry reports.
