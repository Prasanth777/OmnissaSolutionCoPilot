"""Follow-up image editing: interpret a natural-language request about the shown
architecture diagrams and apply it to the current image set.

Supported intents:
- remove     : drop diagrams matching a description ("remove the DMZ image")
- add        : add an eligible diagram matching a description ("add a True SSO diagram")
- replace    : swap DMZ tier then re-pick ("use double DMZ instead")
- regenerate : re-run selection with the current answers
- none       : not an image command (caller should treat it as a Q&A question)
"""
from __future__ import annotations

import json

from .image_select import (
    _canon,
    _eligible,
    _load_arch_rows,
    _relevant,
    _score,
    diagram_profile,
    requirement_profile,
)

_MOD_HINTS = (
    "image", "diagram", "picture", "slide", "figure", "dmz", "uag", "remove", "drop",
    "delete", "add", "include", "replace", "swap", "switch", "regenerate", "redo",
    "show", "use ", "more ", "fewer", "another",
)


def looks_like_image_command(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _MOD_HINTS)


def parse_command(foundry, text: str) -> dict:
    """LLM-extract a structured op. Falls back to {'action': 'none'} on any error."""
    model = foundry.settings.azure_chat_deployment
    base: dict = {"action": "none", "keywords": [], "dmz": "", "indexes": []}
    try:
        resp = foundry._create_chat_completion(
            model=model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": (
                    "You convert a user's request about architecture diagram images into JSON. "
                    "Respond ONLY with compact JSON: "
                    '{"action":"remove|add|replace|regenerate|none",'
                    '"keywords":[..],"dmz":"single|double|",'
                    '"indexes":[1-based ints if the user names image numbers]}. '
                    "remove=take diagrams out; add=bring an extra diagram in; "
                    "replace=change DMZ tier or protocol then re-pick; "
                    "regenerate=rebuild the whole set; none=not about images. "
                    "keywords = short topic words from the request (e.g. dmz, true sso, "
                    "load balancing, network ports, workspace one)."
                )},
                {"role": "user", "content": text},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
    except Exception:
        return base
    out = dict(base)
    action = str(data.get("action", "")).lower()
    if action in ("remove", "add", "replace", "regenerate", "none"):
        out["action"] = action
    out["keywords"] = [str(k).lower() for k in (data.get("keywords") or []) if str(k).strip()]
    dmz = str(data.get("dmz", "")).lower()
    out["dmz"] = dmz if dmz in ("single", "double") else ""
    out["indexes"] = [int(i) for i in (data.get("indexes") or []) if str(i).isdigit()]
    return out


def _row_haystack(row: dict) -> str:
    return " ".join([
        str(row.get("slide_title", "")), str(row.get("figure_caption", "")),
        str(row.get("caption", "")), str(row.get("topic", "")),
        str(row.get("section_heading", "")),
    ]).lower()


def apply_command(
    store,
    command: dict,
    answers: dict,
    selected_products: list,
    reference_urls: list,
    current_rows: list,
    limit: int = 10,
) -> tuple[list, str]:
    """Return (new_rows, message). Edits are applied to current_rows + the v3 store."""
    from .image_select import select_hld_images

    action = command.get("action", "none")
    keywords = command.get("keywords", [])
    rows = list(current_rows)

    if action == "regenerate":
        new_rows = select_hld_images(store, selected_products, answers, reference_urls, limit=limit)
        return new_rows, f"Regenerated the diagram set ({len(new_rows)} diagrams)."

    if action == "replace" and command.get("dmz"):
        updated = dict(answers)
        updated["horizon_dmz_design"] = "Double DMZ" if command["dmz"] == "double" else "Single DMZ"
        new_rows = select_hld_images(store, selected_products, updated, reference_urls, limit=limit)
        return new_rows, f"Switched to {command['dmz']} DMZ and re-selected diagrams."

    if action == "remove":
        idxs = {i - 1 for i in command.get("indexes", [])}
        if idxs:
            new_rows = [r for n, r in enumerate(rows) if n not in idxs]
            return new_rows, f"Removed {len(rows) - len(new_rows)} diagram(s) by position."
        if keywords:
            kept = [r for r in rows if not any(k in _row_haystack(r) for k in keywords)]
            removed = len(rows) - len(kept)
            if removed == 0:
                return rows, "No shown diagram matched that description — nothing removed."
            return kept, f"Removed {removed} diagram(s) matching '{', '.join(keywords)}'."
        return rows, "Tell me which diagram to remove (a number or a topic like 'DMZ')."

    if action == "add":
        if not keywords:
            return rows, "Tell me what to add (e.g. 'add a True SSO diagram')."
        shown = {str(r.get("local_path", "")) for r in rows}
        req = requirement_profile(answers, selected_products)
        ref_set = {_canon(u) for u in (reference_urls or [])}
        candidates = []
        for r in _load_arch_rows(store):
            lp = str(r.get("local_path", ""))
            if lp in shown or not _relevant(r, ref_set):
                continue
            dia = diagram_profile(r)
            if not _eligible(req, dia):
                continue
            if any(k in _row_haystack(r) for k in keywords):
                candidates.append((_score(r, dia, ref_set, req), r))
        if not candidates:
            return rows, f"No eligible diagram found for '{', '.join(keywords)}'."
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = dict(candidates[0][1])
        best["slide_title"] = best.get("caption") or best.get("title")
        return rows + [best], f"Added: {best.get('figure_caption') or best.get('caption', '')[:80]}"

    return rows, ""
