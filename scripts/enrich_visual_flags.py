"""One-time: add per-image VISUAL flags (has_uag/has_dmz/has_workspace_one/...) to the
v3 caption store using the vision model. Resumable (skips rows already enriched)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from sa_hld_bot.azure_foundry import AzureFoundryClient  # noqa: E402
from sa_hld_bot.config import load_settings  # noqa: E402
from sa_hld_bot.image_pipeline import classify_visual_flags  # noqa: E402

FLAGS_VERSION = 1

def main() -> None:
    settings = load_settings(ROOT)
    foundry = AzureFoundryClient(settings)
    path = settings.image_captions_file
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    todo = [r for r in rows if int(r.get("flags_version", 0) or 0) != FLAGS_VERSION]
    print(f"rows: {len(rows)} | needing flags: {len(todo)}")

    done = 0
    counts = {"has_uag": 0, "has_dmz": 0, "has_external_clients": 0, "has_workspace_one": 0, "has_horizon_edge": 0}
    for r in rows:
        if int(r.get("flags_version", 0) or 0) == FLAGS_VERSION:
            continue
        flags = classify_visual_flags(foundry, r)
        r.update(flags)
        r["flags_version"] = FLAGS_VERSION
        for k in counts:
            if r.get(k):
                counts[k] += 1
        done += 1
        if done % 25 == 0:
            print(f"  ...{done}/{len(todo)} enriched")
            with path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\ndone. enriched {done} rows.")
    print("visible-component counts:", counts)

if __name__ == "__main__":
    main()
