"""Tipos de fuente para reglas (Sprint 3) y utilidades de ingestión."""

from __future__ import annotations

RULE_SOURCE_TIERS = frozenset({"official", "homebrew", "dm_notes", "manual"})


def normalize_rule_source_tier(raw: str | None) -> str:
    if not raw:
        return "manual"
    t = raw.strip().lower()
    return t if t in RULE_SOURCE_TIERS else "manual"


def dedupe_near_identical_chunks(chunks: list[str], preview_len: int = 240) -> list[str]:
    """Evita duplicados evidentes tras extracción de PDF."""
    seen: set[str] = set()
    out: list[str] = []
    for c in chunks:
        key = " ".join((c or "").lower().split())[:preview_len]
        if len(key) < 24:
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
