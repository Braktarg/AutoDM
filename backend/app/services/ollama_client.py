"""Cliente HTTP para Ollama (chat + embeddings), sin dependencia del SDK."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from app.config import settings


def _base() -> str:
    return settings.ollama_base_url.rstrip("/")


def ollama_chat(
    messages: list[dict[str, str]],
    *,
    timeout: float | None = None,
    options_extra: dict[str, float | int] | None = None,
) -> str:
    read_s = timeout if timeout is not None else settings.ollama_chat_timeout_seconds
    # Lectura larga (respuesta completa sin stream); conexión más corta.
    httpx_timeout = httpx.Timeout(
        connect=30.0,
        read=read_s,
        write=60.0,
        pool=30.0,
    )
    url = f"{_base()}/api/chat"
    opts: dict[str, float | int] = {"temperature": settings.ollama_temperature}
    if settings.ollama_num_predict is not None:
        opts["num_predict"] = int(settings.ollama_num_predict)
    if settings.ollama_num_ctx is not None:
        opts["num_ctx"] = int(settings.ollama_num_ctx)
    if options_extra:
        opts.update(options_extra)
    body: dict = {
        "model": settings.ollama_llm_model,
        "messages": messages,
        "stream": False,
        "options": opts,
    }
    try:
        with httpx.Client(timeout=httpx_timeout) as client:
            r = client.post(url, json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"No se pudo conectar a Ollama en {_base()}. ¿Está el servicio en marcha?"
        ) from e
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"Ollama no respondió a tiempo (espera hasta {int(read_s)} s para leer la respuesta). "
            "Si usas un modelo pesado o la GPU está cargada, sube OLLAMA_CHAT_TIMEOUT_SECONDS en .env."
        ) from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Ollama respondió error ({e.response.status_code}): {e.response.text[:500]}"
        ) from e
    msg = data.get("message") or {}
    content = msg.get("content") or ""
    return content.strip()


def ollama_chat_stream(
    messages: list[dict[str, str]],
    *,
    timeout: float | None = None,
) -> Iterator[str]:
    read_s = timeout if timeout is not None else settings.ollama_chat_timeout_seconds
    httpx_timeout = httpx.Timeout(
        connect=30.0,
        read=read_s,
        write=60.0,
        pool=30.0,
    )
    url = f"{_base()}/api/chat"
    opts: dict[str, float | int] = {"temperature": settings.ollama_temperature}
    if settings.ollama_num_predict is not None:
        opts["num_predict"] = int(settings.ollama_num_predict)
    if settings.ollama_num_ctx is not None:
        opts["num_ctx"] = int(settings.ollama_num_ctx)
    body: dict = {
        "model": settings.ollama_llm_model,
        "messages": messages,
        "stream": True,
        "options": opts,
    }
    try:
        with httpx.Client(timeout=httpx_timeout) as client:
            with client.stream("POST", url, json=body) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = data.get("message") or {}
                    piece = msg.get("content") or ""
                    if piece:
                        yield str(piece)
                    if data.get("done"):
                        break
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"No se pudo conectar a Ollama en {_base()}. ¿Está el servicio en marcha?"
        ) from e
    except httpx.TimeoutException as e:
        raise RuntimeError(
            f"Ollama no respondió a tiempo (espera hasta {int(read_s)} s para leer la respuesta). "
            "Si usas un modelo pesado o la GPU está cargada, sube OLLAMA_CHAT_TIMEOUT_SECONDS en .env."
        ) from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Ollama respondió error ({e.response.status_code}): {e.response.text[:500]}"
        ) from e


def _embed_one(url: str, text: str, timeout: float) -> list[float]:
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            url,
            json={"model": settings.ollama_embed_model, "prompt": text},
        )
        r.raise_for_status()
        data = r.json()
        emb = data.get("embedding")
        if not emb:
            raise RuntimeError(
                f"Embeddings vacíos (modelo {settings.ollama_embed_model})"
            )
        return list(emb)


def ollama_embed_batch(texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
    """Embeddings vía Ollama; varias peticiones en paralelo (no bloquea minutos en PDFs largos)."""
    if not texts:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed

    url = f"{_base()}/api/embeddings"
    n_workers = min(6, max(1, len(texts)))
    out: list[list[float] | None] = [None] * len(texts)
    try:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            future_map = {
                pool.submit(_embed_one, url, t, timeout): i
                for i, t in enumerate(texts)
            }
            for fut in as_completed(future_map):
                i = future_map[fut]
                out[i] = fut.result()
    except httpx.ConnectError as e:
        raise RuntimeError(
            f"No se pudo conectar a Ollama en {_base()} para embeddings."
        ) from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"Ollama embeddings ({e.response.status_code}): {e.response.text[:400]}"
        ) from e
    if any(v is None for v in out):
        raise RuntimeError("Embeddings incompletos")
    return out  # type: ignore[return-value]
