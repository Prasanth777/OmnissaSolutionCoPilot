"""Reinforcement from reviewer feedback (thumbs up / thumbs down).

Design: every vote is appended to a persistent JSONL store together with the
requirement profile it was given under (access, sites, HA model, DMZ, LB
placement, platform, products) and, for figure-level feedback, the image's
content keys. At selection time the accumulated feedback is folded back into
the figure scoring:

- thumbs UP   -> every figure in the endorsed output earns a positive score
                 adjustment for similar requirement profiles (reinforcement).
- thumbs DOWN on a figure -> strong negative adjustment for similar profiles;
                 repeated downs across different profiles block the figure
                 entirely (it is treated as bad data).
- thumbs DOWN on the document -> the reason text is replayed into future
                 narrative prompts for similar profiles so the generator
                 addresses it ("previous reviewer feedback to address: ...").

Similarity between the stored profile and the current one is the fraction of
matching profile attributes, so learning generalizes across customers with the
same topology rather than being keyed to one exact questionnaire.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

# Profile attributes that define "a similar design" for generalization.
PROFILE_ATTRS = ("access", "sites", "ha_model", "dmz", "lb", "cloud", "hzc_provider", "hzc_gen")

UP_BONUS = 12          # max reinforcement per endorsed figure (scaled by similarity)
DOWN_PENALTY = -45     # max penalty per rejected figure (scaled by similarity)
BLOCK_THRESHOLD = -60  # accumulated penalty at which a figure is excluded outright
GLOBAL_DOWN_BLOCK = 3  # downs across distinct profiles that blocklist a figure


def _feedback_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "feedback.jsonl"


def profile_snapshot(req: dict) -> dict:
    return {attr: str(req.get(attr, "") or "") for attr in PROFILE_ATTRS}


def profile_similarity(stored: dict, current: dict) -> float:
    """Fraction of profile attributes that agree (empty attrs count as neutral 0.5)."""
    score, total = 0.0, 0.0
    for attr in PROFILE_ATTRS:
        a, b = str(stored.get(attr, "")), str(current.get(attr, ""))
        if not a and not b:
            continue
        total += 1.0
        if a == b:
            score += 1.0
        elif not a or not b:
            score += 0.5
    return (score / total) if total else 0.5


def record_feedback(
    data_dir: Path | str,
    vote: str,
    req: dict,
    products: list[str],
    reason: str = "",
    figure_keys: list[dict] | None = None,
    kind: str = "hld",
    session_id: str = "",
) -> None:
    """Append one feedback record. figure_keys items: {md5, ahash, caption, page_url}."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "vote": "up" if vote == "up" else "down",
        "kind": kind,
        "profile": profile_snapshot(req),
        "products": sorted(products or []),
        "reason": str(reason or "")[:1000],
        "figures": figure_keys or [],
        "session_id": session_id,
    }
    path = _feedback_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_feedback(data_dir: Path | str) -> list[dict]:
    path = _feedback_path(data_dir)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def figure_adjustments(data_dir: Path | str, req: dict) -> tuple[dict[str, float], set[str]]:
    """Learned per-figure score deltas for the current requirement profile.

    Returns (adjustments, blocked): adjustments maps an image content key
    (md5 or perceptual hash) to a score delta; blocked is the set of content
    keys that accumulated enough negative signal to be excluded outright.
    """
    current = profile_snapshot(req)
    adjustments: dict[str, float] = {}
    down_profiles: dict[str, set[str]] = {}
    for record in load_feedback(data_dir):
        similarity = profile_similarity(record.get("profile", {}), current)
        weight = UP_BONUS if record.get("vote") == "up" else DOWN_PENALTY
        for figure in record.get("figures", []):
            for key_name in ("md5", "ahash", "cap_key"):
                key = str(figure.get(key_name, "") or "")
                if not key:
                    continue
                adjustments[key] = adjustments.get(key, 0.0) + weight * similarity
                if record.get("vote") == "down":
                    down_profiles.setdefault(key, set()).add(json.dumps(record.get("profile", {}), sort_keys=True))
    blocked = {key for key, delta in adjustments.items() if delta <= BLOCK_THRESHOLD}
    blocked |= {key for key, profiles in down_profiles.items() if len(profiles) >= GLOBAL_DOWN_BLOCK}
    return adjustments, blocked


def narrative_guidance(data_dir: Path | str, req: dict, limit: int = 3) -> list[str]:
    """Recent document-level thumbs-down reasons from similar profiles, most recent
    first, to be replayed into generation prompts."""
    current = profile_snapshot(req)
    scored = []
    for record in load_feedback(data_dir):
        if record.get("vote") != "down" or record.get("kind") != "hld":
            continue
        reason = str(record.get("reason", "")).strip()
        if not reason:
            continue
        similarity = profile_similarity(record.get("profile", {}), current)
        if similarity >= 0.5:
            scored.append((record.get("ts", ""), similarity, reason))
    scored.sort(reverse=True)
    return [reason for _ts, _sim, reason in scored[:limit]]


def match_uploaded_image(data_dir_rows: list[dict], uploaded_bytes: bytes) -> dict | None:
    """Identify which caption-store diagram an uploaded screenshot shows.

    Matches by exact md5 first, then nearest perceptual hash (Hamming distance
    over a 16x16 average hash) with a conservative threshold.
    """
    import hashlib

    md5 = hashlib.md5(uploaded_bytes).hexdigest()
    ahash = _ahash_bytes(uploaded_bytes)

    from .image_select import image_content_keys

    best, best_distance = None, 999
    for row in data_dir_rows:
        local_path = str(row.get("local_path", ""))
        # Fast existence check: image_content_keys retries reads, which would
        # turn a library with stale paths into a multi-minute scan.
        if not local_path or not Path(local_path).exists():
            continue
        row_md5, row_ahash = image_content_keys(local_path)
        if row_md5 and row_md5 == md5:
            return row
        if ahash and row_ahash:
            distance = _hamming_hex(ahash, row_ahash)
            if distance < best_distance:
                best, best_distance = row, distance
    # 256-bit hash; <= 40 differing bits is a confident visual match even across
    # re-encodes, crops of whitespace, or scaling.
    if best is not None and best_distance <= 40:
        return best
    return None


_MATCH_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "uploaded_diagram_match",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "candidate_index": {"type": "integer"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reason": {"type": "string"},
            },
            "required": ["candidate_index", "confidence", "reason"],
        },
    },
}


def _uploaded_data_url(uploaded_bytes: bytes, mime_type: str) -> str:
    mime = mime_type if mime_type in {"image/png", "image/jpeg"} else "image/png"
    encoded = base64.b64encode(uploaded_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _describe_uploaded_diagram(foundry, uploaded_bytes: bytes, mime_type: str) -> str:
    """Extract searchable visible evidence from a pasted diagram."""
    try:
        response = foundry._create_chat_completion(
            model=foundry.settings.azure_vision_deployment or foundry.settings.azure_chat_deployment,
            temperature=0.0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Describe this enterprise architecture diagram for image retrieval. Transcribe the most "
                        "distinctive visible title, figure caption, product/component labels, topology, site model, "
                        "DMZ pattern, and active-active/active-passive wording. Use only visible evidence. "
                        "Return one compact search paragraph and no commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract searchable evidence from this pasted diagram."},
                        {"type": "image_url", "image_url": {"url": _uploaded_data_url(uploaded_bytes, mime_type)}},
                    ],
                },
            ],
        )
        return str(response.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _verify_rag_candidates(foundry, description: str, candidates: list[dict]) -> tuple[int, str, str]:
    payload = [
        {
            "candidate_index": index,
            "caption": row.get("caption", ""),
            "title": row.get("title", ""),
            "page_url": row.get("page_url", ""),
            "retrieval_text": row.get("retrieval_text", ""),
        }
        for index, row in enumerate(candidates)
    ]
    try:
        response = foundry._create_chat_completion(
            model=foundry.settings.azure_chat_deployment,
            temperature=0.0,
            response_format=_MATCH_RESPONSE_FORMAT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Match visible evidence from a pasted architecture diagram to RAG candidates. Choose a "
                        "candidate only when distinctive labels, topology, and availability/DMZ attributes agree. "
                        "Use candidate_index=-1 and confidence=low when evidence is insufficient. Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Visible evidence: {description}\nRAG candidates: {json.dumps(payload)}",
                },
            ],
        )
        raw = str(response.choices[0].message.content or "").replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw)
        return int(parsed.get("candidate_index", -1)), str(parsed.get("confidence", "low")), str(parsed.get("reason", ""))
    except Exception:
        return -1, "low", "Candidate verification was unavailable."


def match_uploaded_image_in_rag(
    store,
    uploaded_bytes: bytes,
    mime_type: str = "image/png",
    rows: list[dict] | None = None,
) -> tuple[dict | None, dict[str, object]]:
    """Match a pasted image to the authoritative Tech Zone diagram library.

    Exact/perceptual fingerprints are attempted first. Re-rendered or cropped
    images then use vision-derived text to query the RAG image collection, and
    a constrained verifier must approve the retrieved candidate.
    """
    library = list(rows if rows is not None else store._load_caption_rows())
    direct = match_uploaded_image(library, uploaded_bytes)
    if direct is not None:
        return direct, {
            "method": "visual fingerprint",
            "confidence": "high",
            "reason": "The pasted image matched the indexed Tech Zone image fingerprint.",
        }

    description = _describe_uploaded_diagram(store.foundry, uploaded_bytes, mime_type)
    if not description:
        return None, {
            "method": "RAG image search",
            "confidence": "low",
            "reason": "Visible diagram evidence could not be extracted.",
        }

    try:
        store._ensure_image_collection_populated(library)
        count = int(store.image_collection.count())
        if count <= 0:
            raise RuntimeError("The RAG image collection is empty")
        vector = store.embedding_service.embed_text(description)
        result = store.image_collection.query(
            query_embeddings=[vector],
            n_results=min(8, count),
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        return None, {
            "method": "RAG image search",
            "confidence": "low",
            "description": description,
            "reason": f"The RAG image search was unavailable: {exc}",
        }

    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    rows_by_path = {str(row.get("local_path", "")): row for row in library}
    candidates: list[dict] = []
    seen_paths: set[str] = set()
    for document, metadata, distance in zip(documents, metadatas, distances):
        local_path = str(metadata.get("local_path", ""))
        if not local_path or local_path in seen_paths or local_path not in rows_by_path:
            continue
        seen_paths.add(local_path)
        candidate = dict(rows_by_path[local_path])
        candidate["retrieval_text"] = str(document or "")
        candidate["rag_distance"] = float(distance)
        candidates.append(candidate)
    if not candidates:
        return None, {
            "method": "RAG image search",
            "confidence": "low",
            "description": description,
            "reason": "No indexed Tech Zone diagram candidates were returned.",
        }

    index, confidence, reason = _verify_rag_candidates(store.foundry, description, candidates)
    match = candidates[index] if confidence in {"high", "medium"} and 0 <= index < len(candidates) else None
    if match is not None:
        match.pop("retrieval_text", None)
        match.pop("rag_distance", None)
    return match, {
        "method": "vision + RAG image search",
        "confidence": confidence,
        "description": description,
        "reason": reason,
        "candidate_count": len(candidates),
    }


def _ahash_bytes(data: bytes) -> str:
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            gray = im.convert("L").resize((16, 16))
            pixels = list(gray.getdata())
            avg = sum(pixels) / len(pixels)
            bits = "".join("1" if p > avg else "0" for p in pixels)
            return f"{int(bits, 2):064x}"
    except Exception:
        return ""


def _hamming_hex(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 999


def caption_key(row: dict) -> str:
    """IO-free identity key from caption + source page (fallback when the image
    file cannot be hashed)."""
    import hashlib

    basis = f"{str(row.get('caption', '')).strip().lower()}|{str(row.get('page_url', '')).strip().lower()}"
    return hashlib.md5(basis.encode("utf-8")).hexdigest() if basis != "|" else ""


def figure_keys_for_rows(rows: list[dict]) -> list[dict]:
    """Content keys + identity for a list of caption rows (for feedback records)."""
    from .image_select import image_content_keys

    keys = []
    for row in rows:
        md5, ahash = image_content_keys(str(row.get("local_path", "")))
        keys.append({
            "md5": md5,
            "ahash": ahash,
            "cap_key": caption_key(row),
            "caption": str(row.get("caption", ""))[:160],
            "page_url": str(row.get("page_url", ""))[:300],
        })
    return keys
