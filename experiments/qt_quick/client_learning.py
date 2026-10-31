"""Persist an explicit human identity label through the original memory store.

The caller owns human intent, capture provenance and lifecycle validation. This
adapter neither infers labels nor grants pose/display authority.
"""
from __future__ import annotations


def persist_manual_confirmation(engine, map_id, floor, frame, bounds):
    """Report write and reload separately: a reload error cannot undo a label."""
    saved = False
    try:
        row = engine.memory_store.record_confirmation(map_id, str(floor), frame, bounds)
        saved = True
        engine.reload_memory()
        return dict(status="saved", saved=True, reloaded=True,
                    hasSample=bool(row.get("samplePath")))
    except Exception as error:
        return dict(status="error", saved=saved, reloaded=False,
                    error=f"{type(error).__name__}:{error}"[:256])
