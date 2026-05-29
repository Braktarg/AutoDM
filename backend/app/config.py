from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AutoDM"
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    database_url: str = "sqlite+aiosqlite:///./data/autodm.db"

    data_dir: Path = Path("./data")
    uploads_dir: Path = Path("./data/uploads")
    chroma_dir: Path = Path("./data/chroma")
    graph_data_dir: Path = Path("./data/graphs")

    # ollama | openai
    llm_provider: Literal["ollama", "openai"] = "ollama"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_llm_model: str = "gemma4:e4b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_temperature: float = 0.85
    # Modelos grandes / primera carga GPU pueden superar 3 min; sube si ves ReadTimeout.
    ollama_chat_timeout_seconds: float = 600.0
    # Límite de tokens generados (Ollama `num_predict`). Menos = respuestas más cortas y suele bajar latencia.
    ollama_num_predict: int | None = None
    # Ventana de contexto del modelo; bajar (p. ej. 4096) puede acelerar en GPUs con poca VRAM.
    ollama_num_ctx: int | None = None

    # Tamaño del prompt de juego (menos chunks / historial = prompt más corto = más rápido).
    dm_rag_rules_k: int = 6
    dm_rag_sheets_k: int = 3
    dm_recent_chat_messages: int = 20
    dm_graph_context_limit: int = 25
    # Instrucción extra al DM para priorizar respuestas breves (mejor flujo en mesa).
    dm_brief_replies: bool = False
    # custom = respeta sólo las variables anteriores. Otros valores aplican perfiles Sprint 1.
    dm_pace: Literal["custom", "fast", "balanced", "cinematic"] = "custom"

    # Sprint 2: cada cuántas respuestas del DM se regenera el resumen de sesión (0 = desactivado).
    dm_session_summary_every_n: int = 8
    dm_consistency_guard: bool = True
    dm_scene_adaptation: bool = True

    # Sprint 3: RAG — candidatos extras + re-ranking (vector + lexical + peso por tipo de fuente).
    rag_rerank_enabled: bool = True
    dm_rag_fetch_multiplier: int = 3
    rag_lexical_weight: float = 0.38
    rag_tier_boost_official: float = 1.14
    rag_tier_boost_dm_notes: float = 1.07
    rag_tier_boost_homebrew: float = 1.0
    rag_tier_boost_manual: float = 1.03
    rag_tier_boost_character: float = 1.04

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"

    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    use_neo4j: bool = False

    chunk_size: int = 1200
    chunk_overlap: int = 200

    # Grafo estilo Obsidian: [[wikilink]] y #tag en chat → nodos + CO_OCCURS
    graph_mentions_enabled: bool = True
    graph_mention_wikilink: bool = True
    graph_mention_hashtag: bool = True
    graph_mention_label: str = "Entity"
    graph_mention_min_len: int = 2
    graph_mention_max_len: int = 80
    graph_mention_cooccurrence: bool = True

    # Sprint 5: runtime narrativo persistente
    # Redis opcional para fan-out de notificaciones SSE multi-worker.
    redis_url: str | None = None
    # Scheduler de mundo (segundos). 0 = desactivado.
    world_tick_interval_seconds: int = 0

    # Pipeline de turno: fast = sin LLM (heurística + efectos fijos, ~instantáneo);
    # hybrid = LLM solo si hace falta (recomendado); full = parse + efectos siempre con LLM.
    dm_action_pipeline: Literal["fast", "hybrid", "full"] = "hybrid"
    # RAG de reglas en cada turno (embeddings). Desactivado = mucho más rápido en play.
    dm_play_rag_on_turn: bool = False
    # Resumen de sesión, eventos narrativos y grafo tras responder (no bloquean el stream).
    dm_play_defer_heavy_work: bool = True
    # LLM del pipeline (parse/efectos): prompts cortos y pocos tokens.
    dm_pipeline_llm_num_predict: int = 320
    dm_pipeline_llm_temperature: float = 0.15
    dm_pipeline_llm_num_ctx: int = 2048

    @model_validator(mode="after")
    def _apply_dm_pace(self) -> Settings:
        if self.dm_pace == "custom":
            return self
        if self.dm_pace == "fast":
            self.dm_brief_replies = True
            self.dm_rag_rules_k = 4
            self.dm_rag_sheets_k = 2
            self.dm_recent_chat_messages = 12
            self.dm_graph_context_limit = 12
            self.ollama_num_predict = 512
            self.dm_action_pipeline = "fast"
            self.dm_play_rag_on_turn = False
            self.dm_play_defer_heavy_work = True
            self.dm_pipeline_llm_num_predict = 256
        elif self.dm_pace == "balanced":
            self.dm_brief_replies = False
            self.dm_rag_rules_k = 6
            self.dm_rag_sheets_k = 3
            self.dm_recent_chat_messages = 20
            self.dm_graph_context_limit = 25
            self.ollama_num_predict = None
        elif self.dm_pace == "cinematic":
            self.dm_brief_replies = False
            self.dm_rag_rules_k = 8
            self.dm_rag_sheets_k = 4
            self.dm_recent_chat_messages = 28
            self.dm_graph_context_limit = 35
            self.ollama_num_predict = None
        return self


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.uploads_dir.mkdir(parents=True, exist_ok=True)
settings.chroma_dir.mkdir(parents=True, exist_ok=True)
settings.graph_data_dir.mkdir(parents=True, exist_ok=True)
