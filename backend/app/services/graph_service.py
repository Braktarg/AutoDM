"""
GraphRAG por mesa: cada campaña tiene grafo aislado.
- Neo4j opcional (use_neo4j + credenciales): nodos/relaciones filtrados por campaign_id.
- Sin Neo4j: persistencia JSON por campaña en graph_data_dir (aislamiento total por archivo).
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config import settings


def _safe_label(label: str) -> str:
    s = "".join(c if c.isalnum() or c == "_" else "_" for c in label.strip())
    return (s[:64] or "Node").strip("_") or "Node"


def _safe_rel(rel: str) -> str:
    s = "".join(c if c.isalnum() or c == "_" else "_" for c in rel.strip().upper())
    return s[:64] or "REL"


class GraphBackend(ABC):
    campaign_id: str

    @abstractmethod
    def upsert_node(
        self,
        label: str,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        pass

    @abstractmethod
    def relate(
        self,
        from_label: str,
        from_name: str,
        rel_type: str,
        to_label: str,
        to_name: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        pass

    @abstractmethod
    def query_text_context(self, topics: list[str], limit: int = 30) -> str:
        """Devuelve texto para el prompt del DM a partir de consultas temáticas."""
        pass

    @abstractmethod
    def apply_event(self, summary: str, tags: list[str] | None = None) -> None:
        pass


class JsonFileGraph(GraphBackend):
    """Grafo por archivo: un JSON por campaign_id."""

    def __init__(self, campaign_id: str) -> None:
        self.campaign_id = campaign_id
        self.path = settings.graph_data_dir / f"{campaign_id}.json"
        self._data: dict[str, Any] = {"nodes": [], "edges": []}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _find_node(self, label: str, name: str) -> dict[str, Any] | None:
        for n in self._data["nodes"]:
            if n.get("label") == label and n.get("name") == name:
                return n
        return None

    def upsert_node(
        self,
        label: str,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        props = dict(properties or {})
        props.setdefault("campaign_id", self.campaign_id)
        existing = self._find_node(label, name)
        if existing:
            existing["properties"].update(props)
            nid = existing["id"]
        else:
            nid = str(uuid.uuid4())
            self._data["nodes"].append(
                {
                    "id": nid,
                    "label": label,
                    "name": name,
                    "properties": props,
                }
            )
        self._save()
        return nid

    def relate(
        self,
        from_label: str,
        from_name: str,
        rel_type: str,
        to_label: str,
        to_name: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        a = self._find_node(from_label, from_name)
        b = self._find_node(to_label, to_name)
        if not a:
            self.upsert_node(from_label, from_name)
            a = self._find_node(from_label, from_name)
        if not b:
            self.upsert_node(to_label, to_name)
            b = self._find_node(to_label, to_name)
        assert a and b
        rel_clean = _safe_rel(rel_type)
        new_props = dict(properties or {})
        for e in self._data["edges"]:
            if (
                e["from_id"] == a["id"]
                and e["to_id"] == b["id"]
                and e["type"] == rel_clean
            ):
                return
        self._data["edges"].append(
            {
                "from_id": a["id"],
                "to_id": b["id"],
                "type": rel_clean,
                "properties": new_props,
            }
        )
        self._save()

    def query_text_context(self, topics: list[str], limit: int = 30) -> str:
        topic_set = {t.lower() for t in topics}
        lines: list[str] = []
        for n in self._data["nodes"][:limit]:
            blob = f"{n['label']}: {n['name']} — {n.get('properties', {})}"
            if any(t in blob.lower() for t in topic_set) or not topic_set:
                lines.append(blob)
        for e in self._data["edges"][:limit]:
            fa = next((x for x in self._data["nodes"] if x["id"] == e["from_id"]), None)
            tb = next((x for x in self._data["nodes"] if x["id"] == e["to_id"]), None)
            if fa and tb:
                lines.append(f"{fa['name']} -[{e['type']}]-> {tb['name']}")
        return "\n".join(lines[:limit]) if lines else "(grafo aún vacío en esta mesa)"

    def apply_event(self, summary: str, tags: list[str] | None = None) -> None:
        self.upsert_node(
            "Event",
            f"event_{len(self._data['nodes'])}",
            {"summary": summary, "tags": tags or []},
        )
        self._save()


class Neo4jGraph(GraphBackend):
    def __init__(self, campaign_id: str) -> None:
        from neo4j import GraphDatabase

        self.campaign_id = campaign_id
        if not settings.neo4j_uri or not settings.neo4j_user:
            raise RuntimeError("Neo4j no configurado")
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password or ""),
        )

    def upsert_node(
        self,
        label: str,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        lab = _safe_label(label)
        props = dict(properties or {})
        props["name"] = name
        props["campaign_id"] = self.campaign_id
        q = f"""
        MERGE (n:`{lab}` {{campaign_id: $cid, name: $name}})
        SET n += $props
        RETURN elementId(n) AS id
        """
        with self._driver.session() as session:
            r = session.run(
                q, cid=self.campaign_id, name=name, props=props
            ).single()
            return str(r["id"]) if r else ""

    def relate(
        self,
        from_label: str,
        from_name: str,
        rel_type: str,
        to_label: str,
        to_name: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        la = _safe_label(from_label)
        lb = _safe_label(to_label)
        rel_clean = _safe_rel(rel_type)
        q = f"""
        MATCH (a:`{la}` {{campaign_id: $cid, name: $an}})
        MATCH (b:`{lb}` {{campaign_id: $cid, name: $bn}})
        MERGE (a)-[r:`{rel_clean}`]->(b)
        SET r += $props
        """
        with self._driver.session() as session:
            session.run(
                q,
                cid=self.campaign_id,
                an=from_name,
                bn=to_name,
                props=dict(properties or {}),
            )

    def query_text_context(self, topics: list[str], limit: int = 30) -> str:
        # Búsqueda simple por texto en nodos de esta campaña
        q = """
        MATCH (n)
        WHERE n.campaign_id = $cid
        AND (
          ANY(t IN $topics WHERE toLower(coalesce(n.name,'')) CONTAINS t OR toLower(coalesce(n.summary,'')) CONTAINS t)
          OR size($topics) = 0
        )
        RETURN labels(n)[0] as L, n.name as name, properties(n) as p
        LIMIT $limit
        """
        topics_l = [t.lower() for t in topics]
        lines: list[str] = []
        with self._driver.session() as session:
            for row in session.run(q, cid=self.campaign_id, topics=topics_l, limit=limit):
                lines.append(f"{row['L']}: {row['name']} — {row['p']}")
        return "\n".join(lines) if lines else "(sin nodos coincidentes)"

    def apply_event(self, summary: str, tags: list[str] | None = None) -> None:
        name = f"event_{uuid.uuid4().hex[:8]}"
        self.upsert_node(
            "Event",
            name,
            {"summary": summary, "tags": tags or []},
        )


def get_graph_backend(campaign_id: str) -> GraphBackend:
    if settings.use_neo4j and settings.neo4j_uri:
        return Neo4jGraph(campaign_id)
    return JsonFileGraph(campaign_id)


def export_world_summary(campaign_id: str) -> dict[str, Any]:
    backend = get_graph_backend(campaign_id)
    if isinstance(backend, JsonFileGraph):
        nodes = backend._data.get("nodes", [])
        by_label: dict[str, list[dict[str, Any]]] = {}
        for n in nodes:
            lab = str(n.get("label", "Node"))
            by_label.setdefault(lab, []).append(
                {
                    "name": n.get("name"),
                    "properties": n.get("properties", {}),
                }
            )
        events = by_label.get("Event", [])[-8:][::-1]
        return {
            "nodes_by_label": by_label,
            "edge_count": len(backend._data.get("edges", [])),
            "recent_events": events,
        }
    if isinstance(backend, Neo4jGraph):
        q = """
        MATCH (n)
        WHERE n.campaign_id = $cid
        RETURN labels(n)[0] AS L, n.name AS name, properties(n) AS p
        LIMIT 120
        """
        by_label: dict[str, list[dict[str, Any]]] = {}
        with backend._driver.session() as session:
            for row in session.run(q, cid=campaign_id):
                lab = row["L"] or "Node"
                by_label.setdefault(str(lab), []).append(
                    {
                        "name": row["name"],
                        "properties": dict(row["p"] or {}),
                    }
                )
        ev = by_label.get("Event", [])[-8:][::-1]
        return {
            "nodes_by_label": by_label,
            "edge_count": 0,
            "recent_events": ev,
        }
    return {"nodes_by_label": {}, "edge_count": 0, "recent_events": []}


def project_effects_to_graph(
    campaign_id: str,
    *,
    player_display_name: str | None,
    action_request: dict[str, Any],
    action_effects: dict[str, Any],
) -> dict[str, Any]:
    """
    Proyecta efectos validados al GraphRAG como relaciones tipadas.
    v1: proyección mínima desde ActionRequest/ActionEffects (no keywords).
    """
    g = get_graph_backend(campaign_id)
    action_type = str(action_request.get("action_type") or "unknown").lower()
    targets = [str(t).strip() for t in (action_request.get("targets") or []) if str(t).strip()]
    items_used = action_effects.get("inventory_consumptions") or []

    actor = (player_display_name or "").strip() or "Jugador"

    # Evento/timestamp lógico por turno (sin reloj global exacto).
    turn_event_name = f"evt_{uuid.uuid4().hex[:8]}"
    g.upsert_node(
        "Event",
        turn_event_name,
        {"event_type": f"turn_{action_type}", "summary": action_effects.get("block_reason") or "turn", "at": None},
    )

    # Actor -> Event
    g.upsert_node("Entity", actor, {"from_player": True})
    g.relate("Entity", actor, "INVOLVED_IN", "Event", turn_event_name)

    # Relaciones con targets (si el parser los extrajo)
    rel_map = {
        "attack": "ATTACKED",
        "talk": "INTERACTED",
        "explore": "EXPLORED",
        "use_item": "USED",
        "cast_spell": "CAST_SPELL",
    }
    rel = rel_map.get(action_type, action_type.upper())
    for t in targets:
        g.upsert_node("Entity", t)
        g.relate("Entity", actor, rel, "Entity", t, {"at": None})
        g.relate("Entity", t, "INVOLVED_IN", "Event", turn_event_name, {"at": None})

    # Proyección de inventario consumido como Items.
    for c in items_used:
        item_name = str(c.get("item_name") or "").strip()
        qty = c.get("quantity") or 0
        if not item_name:
            continue
        item_label = "Item"
        g.upsert_node(item_label, item_name, {"quantity_consumed": qty})
        g.relate("Entity", actor, "CONSUMED", item_label, item_name, {"qty": qty})

    return {
        "action_type": action_type,
        "targets": targets,
        "items_consumed": [str(c.get("item_name") or "").strip() for c in items_used],
    }


def query_relevant_graph_context(
    campaign_id: str,
    *,
    action_request: dict[str, Any],
    action_effects: dict[str, Any],
    world_state: dict[str, Any] | None = None,
    limit: int = 25,
) -> str:
    """
    Construye tópicos relevantes para el prompt del DM desde la intención/efectos.
    (v1) Devuelve una búsqueda simple por nombre/keywords en el grafo.
    """
    g = get_graph_backend(campaign_id)
    action_type = str(action_request.get("action_type") or "").lower()
    targets = [str(t).strip() for t in (action_request.get("targets") or []) if str(t).strip()]
    items = [
        str(c.get("item_name") or "").strip()
        for c in (action_effects.get("inventory_consumptions") or [])
        if str(c.get("item_name") or "").strip()
    ]

    topics: list[str] = []
    if action_type:
        topics.append(action_type)
    topics.extend(targets[:6])
    topics.extend(items[:6])

    scene_val = (world_state or {}).get("scene")
    if isinstance(scene_val, dict):
        summary = (scene_val or {}).get("summary")
        if summary:
            topics.append(str(summary)[:160])
    elif isinstance(scene_val, str) and scene_val.strip():
        topics.append(scene_val.strip()[:160])

    return g.query_text_context(topics, limit=limit)
