from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.services.ollama_client import ollama_chat


_PLAYER_STATE_SCHEMA_HINT = """
Devuelve un JSON con exactamente estas claves:
{
  "version": 1,
  "hp": {"current": number|null, "max": number|null},
  "conditions": string[],
  "resources": Record<string,number|string|null>,
  "equipment": string[],
  "skills": string[],
  "spells": string[],
  "magic_known": string[],
  "uncertain_fields": string[]
}
Usa null cuando no haya dato claro. En caso de duda, agrega el campo a `uncertain_fields`.
No incluyas texto fuera del JSON.
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"```\s*$", "", t).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    if t.startswith("{"):
        return json.loads(t)
    raise ValueError("No se encontró un objeto JSON en la salida.")


def extract_player_state_sync(sheet_text: str, *, player_name: str) -> dict[str, Any]:
    sheet_text = (sheet_text or "").strip()
    if not sheet_text:
        return {
            "version": 1,
            "hp": {"current": None, "max": None},
            "conditions": [],
            "resources": {},
            "equipment": [],
            "skills": [],
            "spells": [],
            "magic_known": [],
            "uncertain_fields": ["empty_sheet_text"],
        }

    # Si no hay proveedor LLM para parsing, retornamos “incierto”.
    if settings.llm_provider != "ollama" and not settings.openai_api_key:
        return {
            "version": 1,
            "hp": {"current": None, "max": None},
            "conditions": [],
            "resources": {},
            "equipment": [],
            "skills": [],
            "spells": [],
            "magic_known": [],
            "uncertain_fields": ["llm_missing_for_extraction"],
        }

    system = "Eres un extractor de estado de personaje para un motor de rol persistente."
    user = f"""
Extrae el estado operativo del personaje desde el texto de la ficha.

Nombre del jugador/personaje (si aparece): {player_name}

{_PLAYER_STATE_SCHEMA_HINT}

Texto de ficha (puede contener desorden, respeta lo que sea claro):
\"\"\"\n{sheet_text[:9000]}\n\"\"\"
"""
    raw = ollama_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    data = _extract_json_object(raw)
    # Normalizar claves faltantes.
    data.setdefault("version", 1)
    data.setdefault("hp", {"current": None, "max": None})
    data.setdefault("conditions", [])
    data.setdefault("resources", {})
    data.setdefault("equipment", [])
    data.setdefault("skills", [])
    data.setdefault("spells", [])
    data.setdefault("magic_known", [])
    data.setdefault("uncertain_fields", [])
    return data

