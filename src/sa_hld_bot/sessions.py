"""Lightweight persistence for generated HLD runs.

Each completed run is saved as a JSON file under data/sessions/ so it can be
listed in the sidebar and reopened later. Stores everything needed to restore
the preview and continue follow-up Q&A / image edits without regenerating.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _sessions_dir(data_dir) -> Path:
    d = Path(data_dir) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(data_dir, payload: dict, session_id: str | None = None) -> str:
    """Write (or overwrite) a session JSON; returns its id."""
    d = _sessions_dir(data_dir)
    sid = session_id or payload.get("id") or datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dict(payload)
    out["id"] = sid
    out.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    out["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (d / f"{sid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return sid


def list_sessions(data_dir) -> list[dict]:
    """Return session metadata, newest first."""
    out: list[dict] = []
    for f in _sessions_dir(data_dir).glob("*.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": p.get("id", f.stem),
            "title": p.get("title", f.stem),
            "created_at": p.get("created_at", ""),
            "selected_products": p.get("selected_products", []),
        })
    out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return out


def load_session(data_dir, sid: str) -> dict | None:
    f = _sessions_dir(data_dir) / f"{sid}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_session(data_dir, sid: str) -> bool:
    f = _sessions_dir(data_dir) / f"{sid}.json"
    try:
        f.unlink()
        return True
    except Exception:
        return False
