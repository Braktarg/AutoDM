"""
Elimina artefactos en disco y vectores asociados a una campaña (borrado de mesa).
"""

from __future__ import annotations

import shutil

from app.config import settings


def _rules_collection_name(campaign_id: str) -> str:
    return f"rules_{campaign_id}"


def _sheets_collection_name(campaign_id: str) -> str:
    return f"character_sheets_{campaign_id}"


def _delete_chroma_collections(campaign_id: str) -> None:
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.PersistentClient(
            path=str(settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        for name in (
            _rules_collection_name(campaign_id),
            _sheets_collection_name(campaign_id),
        ):
            try:
                client.delete_collection(name=name)
            except Exception:
                pass
    except Exception:
        pass


def _delete_graph_files(campaign_id: str) -> None:
    path = settings.graph_data_dir / f"{campaign_id}.json"
    if path.exists():
        path.unlink()


def _purge_neo4j_campaign(campaign_id: str) -> None:
    if not settings.use_neo4j or not settings.neo4j_uri or not settings.neo4j_user:
        return
    try:
        # Import perezoso: neo4j puede arrastrar pandas/numpy del site-packages global;
        # si hay ABI rotas, no debe impedir arrancar la API cuando Neo4j está desactivado.
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password or ""),
        )
        try:
            with driver.session() as session:
                session.run(
                    "MATCH (n) WHERE n.campaign_id = $cid DETACH DELETE n",
                    cid=campaign_id,
                )
        finally:
            driver.close()
    except Exception:
        pass


def purge_campaign_side_effects(campaign_id: str) -> None:
    """Chroma, grafo (archivo y Neo4j) y carpeta de uploads de la mesa."""
    _delete_chroma_collections(campaign_id)
    _delete_graph_files(campaign_id)
    _purge_neo4j_campaign(campaign_id)
    upload_root = settings.uploads_dir / campaign_id
    if upload_root.is_dir():
        shutil.rmtree(upload_root, ignore_errors=True)
