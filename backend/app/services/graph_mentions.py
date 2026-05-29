"""
Ingesta tipo Obsidian: [[wikilinks]] y #tags en el texto del chat → nodos Entity + aristas CO_OCCURS.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.services.graph_service import get_graph_backend

# [[página]] o [[página|alias]] — se usa el nombre de página (no el alias).
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# #etiqueta / #padre/hijo (estilo Obsidian). No captura ## encabezados Markdown (el # tras otro #).
_HASHTAG_RE = re.compile(
    r"(?<!\#)\#([\w\u00C0-\u024F][\w\u00C0-\u024F/]*)",
    re.UNICODE,
)


def _entity_label() -> str:
    s = _safe_graph_label(settings.graph_mention_label.strip() or "Entity")
    return s or "Entity"


def _safe_graph_label(label: str) -> str:
    t = "".join(c if c.isalnum() or c == "_" else "_" for c in label.strip())
    return (t[:64] or "Entity").strip("_") or "Entity"


def normalize_mention_name(raw: str) -> str | None:
    name = " ".join(raw.strip().split())
    lo = settings.graph_mention_min_len
    hi = settings.graph_mention_max_len
    if len(name) < lo or len(name) > hi:
        return None
    if name.isdigit():
        return None
    return name


def extract_mention_names(text: str | None) -> list[tuple[str, str]]:
    """
    Devuelve lista de (origen, nombre_normalizado) con origen 'wiki' | 'tag'.
    Sin duplicados conservando el primer orden de aparición.
    """
    if not text or not settings.graph_mentions_enabled:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    if settings.graph_mention_wikilink:
        for m in _WIKILINK_RE.finditer(text):
            raw = m.group(1).strip()
            n = normalize_mention_name(raw)
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(("wiki", n))

    if settings.graph_mention_hashtag:
        for m in _HASHTAG_RE.finditer(text):
            raw = m.group(1).strip()
            n = normalize_mention_name(raw)
            if n and n.lower() not in seen:
                seen.add(n.lower())
                out.append(("tag", n))

    return out


def apply_graph_mentions(
    campaign_id: str,
    text: str | None,
    *,
    role: str,
) -> dict[str, Any]:
    """
    Crea/actualiza nodos Entity y aristas CO_OCCURS entre menciones en el mismo texto.
    role: 'user' | 'assistant' (solo metadatos en el nodo).
    """
    if not settings.graph_mentions_enabled:
        return {"enabled": False, "names": [], "nodes_upserted": 0, "edges_added": 0}

    pairs = extract_mention_names(text)
    if not pairs:
        return {
            "enabled": True,
            "names": [],
            "nodes_upserted": 0,
            "edges_added": 0,
        }

    label = _entity_label()
    names = [n for _, n in pairs]
    graph = get_graph_backend(campaign_id)

    nodes_n = 0
    for origin, name in pairs:
        props: dict[str, Any] = {
            "from_mention": True,
            "last_mention_role": role,
        }
        if origin == "wiki":
            props["via_wikilink"] = True
        else:
            props["via_hashtag"] = True
        graph.upsert_node(label, name, props)
        nodes_n += 1

    edges_n = 0
    if settings.graph_mention_cooccurrence and len(names) >= 2:
        uniq_sorted = sorted(set(names), key=lambda x: x.lower())
        for i in range(len(uniq_sorted)):
            for j in range(i + 1, len(uniq_sorted)):
                graph.relate(
                    label,
                    uniq_sorted[i],
                    "CO_OCCURS",
                    label,
                    uniq_sorted[j],
                )
                edges_n += 1

    return {
        "enabled": True,
        "names": names,
        "nodes_upserted": nodes_n,
        "edges_added": edges_n,
    }
