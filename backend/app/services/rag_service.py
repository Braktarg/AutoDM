"""
RAG aislado por campaña: cada mesa usa colecciones Chroma con ID derivado del campaign_id.
Sprint 3: re-ranking híbrido, metadatos source_tier, trazas para la UI.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from openai import OpenAI

from app.config import settings
from app.services.ollama_client import ollama_embed_batch
from app.services.rag_sources import normalize_rule_source_tier


def _client() -> Any:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(
        path=str(settings.chroma_dir),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _rules_collection_name(campaign_id: str) -> str:
    return f"rules_{campaign_id}"


def _sheets_collection_name(campaign_id: str) -> str:
    return f"character_sheets_{campaign_id}"


def _embed_texts(texts: list[str]) -> list[list[float]]:
    if settings.llm_provider == "ollama":
        return ollama_embed_batch(texts)
    if not settings.openai_api_key:
        dim = 1536
        return [
            [(hash(t + str(i)) % 1000) / 1000.0 for _ in range(dim)]
            for i, t in enumerate(texts)
        ]
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    r = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in r.data]


def _tier_multiplier(tier: str | None, *, is_sheet: bool) -> float:
    if is_sheet:
        return float(settings.rag_tier_boost_character)
    t = (tier or "manual").strip().lower()
    if t == "official":
        return float(settings.rag_tier_boost_official)
    if t == "dm_notes":
        return float(settings.rag_tier_boost_dm_notes)
    if t == "homebrew":
        return float(settings.rag_tier_boost_homebrew)
    return float(settings.rag_tier_boost_manual)


def _lexical_overlap(query: str, doc: str) -> float:
    qw = set(re.findall(r"\w{3,}", query.lower()))
    dw = set(re.findall(r"\w{3,}", doc.lower()))
    if not qw:
        return 0.0
    return len(qw & dw) / len(qw)


def _vector_component(distance: float | None, rank: int) -> float:
    if distance is not None and distance == distance:  # not NaN
        d = max(0.0, float(distance))
        sim = max(0.0, min(1.0, 1.0 - d))
        return sim
    return 1.0 / (1.0 + rank)


def _rerank_documents(
    query: str,
    documents: list[str],
    metadatas: list[dict[str, Any]],
    distances: list[float | None],
    *,
    final_k: int,
    is_sheet: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    w_lex = float(settings.rag_lexical_weight)
    w_vec = max(0.0, min(1.0, 1.0 - w_lex))
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, doc in enumerate(documents):
        if not doc:
            continue
        md = dict(metadatas[i]) if metadatas and i < len(metadatas) else {}
        dist = distances[i] if distances and i < len(distances) else None
        tier = md.get("source_tier") or ("character" if is_sheet else "manual")
        tier_m = _tier_multiplier(tier, is_sheet=is_sheet)
        lex = _lexical_overlap(query, doc)
        vec_c = _vector_component(dist, i)
        base = w_vec * vec_c + w_lex * lex
        combined = tier_m * max(1e-6, base)
        item = {"text": doc, "metadata": md}
        preview = doc.replace("\n", " ").strip()
        if len(preview) > 220:
            preview = preview[:217] + "…"
        trace = {
            "source": md.get("source", ""),
            "source_tier": tier,
            "preview": preview,
            "rerank_score": round(combined, 4),
        }
        scored.append((combined, item, trace))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:final_k]
    items = [x[1] for x in top]
    traces = [x[2] for x in top]
    return items, traces


def upsert_rule_chunks(
    campaign_id: str,
    chunks: list[str],
    source_label: str,
    *,
    source_tier: str = "manual",
) -> int:
    if not chunks:
        return 0
    tier = normalize_rule_source_tier(source_tier)
    coll = _client().get_or_create_collection(
        name=_rules_collection_name(campaign_id),
        metadata={"hnsw:space": "cosine", "campaign_id": campaign_id},
    )
    ids = [str(uuid.uuid4()) for _ in chunks]
    embeddings = _embed_texts(chunks)
    metadatas = [
        {"source": source_label, "campaign_id": campaign_id, "source_tier": tier}
        for _ in chunks
    ]
    try:
        coll.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
    except Exception as e:
        err = str(e).lower()
        if "dimension" in err or "embedding" in err:
            try:
                _client().delete_collection(name=_rules_collection_name(campaign_id))
            except Exception:
                pass
            coll = _client().get_or_create_collection(
                name=_rules_collection_name(campaign_id),
                metadata={"hnsw:space": "cosine", "campaign_id": campaign_id},
            )
            coll.add(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
        else:
            raise
    return len(chunks)


def clear_rules_for_source(campaign_id: str, source_label: str) -> None:
    try:
        coll = _client().get_or_create_collection(
            name=_rules_collection_name(campaign_id)
        )
        data = coll.get(where={"source": source_label})
        if data and data.get("ids"):
            coll.delete(ids=data["ids"])
    except Exception:
        pass


def upsert_character_sheet(
    campaign_id: str,
    user_id: str,
    chunks: list[str],
    display_name: str,
) -> int:
    coll = _client().get_or_create_collection(
        name=_sheets_collection_name(campaign_id),
        metadata={"hnsw:space": "cosine", "campaign_id": campaign_id},
    )
    source_label = f"sheet:{user_id}"
    try:
        existing = coll.get(where={"source": source_label})
        if existing and existing.get("ids"):
            coll.delete(ids=existing["ids"])
    except Exception:
        pass
    if not chunks:
        return 0
    ids = [str(uuid.uuid4()) for _ in chunks]
    embeddings = _embed_texts(chunks)
    metadatas = [
        {
            "source": source_label,
            "campaign_id": campaign_id,
            "user_id": user_id,
            "display_name": display_name,
            "source_tier": "character",
        }
        for _ in chunks
    ]
    try:
        coll.add(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
    except Exception as e:
        err = str(e).lower()
        if "dimension" in err or "embedding" in err:
            try:
                _client().delete_collection(
                    name=_sheets_collection_name(campaign_id)
                )
            except Exception:
                pass
            coll = _client().get_or_create_collection(
                name=_sheets_collection_name(campaign_id),
                metadata={"hnsw:space": "cosine", "campaign_id": campaign_id},
            )
            coll.add(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            )
        else:
            raise
    return len(chunks)


def retrieve_rules(
    campaign_id: str, query: str, k: int = 6
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    coll = _client().get_or_create_collection(name=_rules_collection_name(campaign_id))
    q_emb = _embed_texts([query])[0]

    multiplier = max(1, settings.dm_rag_fetch_multiplier)
    fetch_n = min(max(k * multiplier, k), 40)

    if not settings.rag_rerank_enabled:
        fetch_n = k

    res = coll.query(
        query_embeddings=[q_emb],
        n_results=fetch_n,
        where={"campaign_id": campaign_id},
        include=["documents", "distances", "metadatas"],
    )
    docs = (res["documents"] or [[]])[0] or []
    metas_raw = res.get("metadatas") or [[]]
    metas = (metas_raw[0] or []) if metas_raw else []
    dists_wrapped = res.get("distances") or [[]]
    dist_list = dists_wrapped[0] if dists_wrapped else []
    distances: list[float | None] = []
    for d in dist_list:
        try:
            distances.append(float(d) if d is not None else None)
        except (TypeError, ValueError):
            distances.append(None)
    while len(distances) < len(docs):
        distances.append(None)

    items: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []

    if not docs:
        return items, traces

    if not settings.rag_rerank_enabled or len(docs) <= k:
        for i, doc in enumerate(docs[:k]):
            md = dict(metas[i]) if i < len(metas) else {}
            tier = md.get("source_tier") or "manual"
            preview = doc.replace("\n", " ").strip()
            if len(preview) > 220:
                preview = preview[:217] + "…"
            items.append({"text": doc, "metadata": md})
            traces.append(
                {
                    "source": md.get("source", ""),
                    "source_tier": tier,
                    "preview": preview,
                    "rerank_score": None,
                }
            )
        return items, traces

    return _rerank_documents(
        query, docs, metas, distances, final_k=k, is_sheet=False
    )


def retrieve_sheets(
    campaign_id: str, query: str, k: int = 4
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    coll = _client().get_or_create_collection(
        name=_sheets_collection_name(campaign_id)
    )
    q_emb = _embed_texts([query])[0]
    multiplier = max(1, settings.dm_rag_fetch_multiplier)
    fetch_n = min(max(k * multiplier, k), 40)

    if not settings.rag_rerank_enabled:
        fetch_n = k

    res = coll.query(
        query_embeddings=[q_emb],
        n_results=fetch_n,
        where={"campaign_id": campaign_id},
        include=["documents", "distances", "metadatas"],
    )
    docs = (res["documents"] or [[]])[0] or []
    metas_raw = res.get("metadatas") or [[]]
    metas = (metas_raw[0] or []) if metas_raw else []
    dists_wrapped = res.get("distances") or [[]]
    dist_list = dists_wrapped[0] if dists_wrapped else []
    distances = []
    for d in dist_list:
        try:
            distances.append(float(d) if d is not None else None)
        except (TypeError, ValueError):
            distances.append(None)
    while len(distances) < len(docs):
        distances.append(None)

    items: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []

    if not docs:
        return items, traces

    if not settings.rag_rerank_enabled or len(docs) <= k:
        for i, doc in enumerate(docs[:k]):
            md = dict(metas[i]) if i < len(metas) else {}
            tier = md.get("source_tier") or "character"
            preview = doc.replace("\n", " ").strip()
            if len(preview) > 220:
                preview = preview[:217] + "…"
            items.append({"text": doc, "metadata": md})
            traces.append(
                {
                    "source": md.get("display_name") or md.get("source", ""),
                    "source_tier": tier,
                    "preview": preview,
                    "rerank_score": None,
                }
            )
        return items, traces

    return _rerank_documents(
        query, docs, metas, distances, final_k=k, is_sheet=True
    )


def retrieve_all_context(
    campaign_id: str, query: str, rules_k: int = 6, sheets_k: int = 3
) -> dict[str, Any]:
    rules, rules_trace = retrieve_rules(campaign_id, query, k=rules_k)
    sheets, sheets_trace = retrieve_sheets(campaign_id, query, k=sheets_k)
    return {
        "rules": rules,
        "character_context": sheets,
        "rules_trace": rules_trace,
        "sheets_trace": sheets_trace,
    }
