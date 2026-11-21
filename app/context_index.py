"""Local keyword retrieval across fetched portal sections, tables, notices, and PDFs."""

import re
from typing import Any, Dict, List


def _tokenize(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2]


def retrieve_relevant_context(
    sites: List[Dict[str, Any]],
    query: str = "",
    state: str = "",
    crop: str = "",
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Rank and return compact, high-signal excerpts from live websites and downloaded PDFs."""
    terms = set(_tokenize(f"{query} {state} {crop}"))
    scored = []

    for site in sites:
        if site.get("status") != "ok":
            continue

        notices = [n.get("text", "") for n in (site.get("notices") or [])]
        sections = [
            f"{sec.get('heading', '')}: {sec.get('text', '')}"
            for sec in (site.get("sections") or [])
        ]
        tables = [" | ".join(row) for row in (site.get("tables") or [])]
        files = [
            f"[File: {f.get('name', '')}] {f.get('textExcerpt', '')[:500]}"
            for f in (site.get("files") or [])
        ]

        full_blob = " ".join(
            [
                site.get("name", ""),
                site.get("title", ""),
                *notices,
                *sections,
                *tables,
                *files,
                (site.get("bodyText") or "")[:800],
            ]
        ).lower()

        score = sum(2 for t in terms if t in full_blob)
        if state and state.lower() in full_blob:
            score += 4
        if crop and crop.lower() in full_blob:
            score += 3
        if notices or files:
            score += 1

        scored.append(
            (
                score,
                {
                    "name": site.get("name"),
                    "url": site.get("url"),
                    "notices": notices[:3],
                    "sections": sections[:2],
                    "tables": tables[:2],
                    "files": files[:2],
                    "excerpt": (site.get("bodyText") or "")[:400],
                },
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:top_k]]
