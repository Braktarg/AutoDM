from __future__ import annotations

import json
import logging
import re
import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from app.config import settings
from app.services.ollama_client import ollama_chat
from app.services.rag_service import retrieve_rules

logger = logging.getLogger(__name__)

_PIPELINE_CTX_LIMIT = 1800

ActionType = Literal[
    "attack", "explore", "talk", "use_item", "cast_spell", "extreme", "unknown"
]


_ACTION_REQUEST_SCHEMA_HINT = """
Devuelve SOLO un JSON válido con estas claves:
{
  "version": 1,
  "action_type": "attack|explore|talk|use_item|cast_spell|extreme|unknown",
  "intents": string[],
  "items_used": [{"name": string, "declared_quantity": number|null}],
  "spells_or_skills": [{"name": string}],
  "targets": string[],
  "extreme_flags": {"nuclear": boolean, "transformation": boolean, "magic_level_undefined": boolean},
  "claims": string[],
  "missing_fields": string[],
  "confidence": number
}
Reglas:
- NO inventes items/spells/skills.
- Si algo no aparece claro en la acción del jugador, marca missing_fields.
- Si el jugador saca/empuña/desenvaina un objeto que figura en el inventario listado, usa action_type=use_item (NO extreme).
- items_used.declared_quantity=0 significa equipar/sacar sin consumir el objeto.
"""


_ACTION_EFFECTS_SCHEMA_HINT = """
Devuelve SOLO un JSON válido con estas claves:
{
  "version": 1,
  "allowed": boolean,
  "block_reason": string|null,
  "needs_player_clarification": boolean,
  "needs_roll": boolean,
  "inventory_consumptions": [{"item_name": string, "quantity": number}],
  "player_state_delta": {"hp": {"current": number|null, "max": number|null}, "conditions_add": string[], "conditions_remove": string[]},
  "world_state_delta": {"scene": string, "time": {"tick": number}},
  "narration_facts": string[],
  "dm_questions": string[]
}
Reglas:
- narration_facts debe contener hechos observables y/o cambios aplicados por inventario/estado.
- Si falta información, allowed=false o needs_player_clarification=true; pero NO inventes resultados.
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
    raise ValueError("No se encontró JSON en la salida del LLM.")


def _normalize_name(s: str) -> str:
    return " ".join((s or "").strip().split())


def _inventory_map(inventory_items: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in inventory_items:
        n = _normalize_name(str(it.get("name") or ""))
        if not n:
            continue
        qty = int(it.get("quantity") or 0)
        out[n.lower()] = out.get(n.lower(), 0) + qty
    return out


def _item_base_key(name: str) -> str:
    """Nombre base sin bonificadores (+1, etc.) para emparejar con el texto del jugador."""
    n = _normalize_name(name).lower()
    return re.sub(r"\s*\+\s*\d+.*$", "", n).strip()


def _resolve_inventory_item(
    query: str, inventory_items: list[dict[str, Any]]
) -> tuple[str, int] | None:
    q = _item_base_key(query)
    if len(q) < 2:
        return None
    best: tuple[str, int, int] | None = None  # canon, qty, base_len
    for it in inventory_items:
        canon = _normalize_name(str(it.get("name") or ""))
        if not canon:
            continue
        base = _item_base_key(canon)
        if len(base) < 2:
            continue
        qty = int(it.get("quantity") or 0)
        if q == base or (len(q) >= 4 and (q in base or base in q)):
            score = len(base)
            if best is None or score < best[2]:
                best = (canon, qty, score)
    if best is None:
        return None
    return best[0], best[1]


def _items_mentioned_in_text(
    player_text: str, inventory_items: list[dict[str, Any]]
) -> list[tuple[str, int]]:
    t = (player_text or "").lower()
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for it in inventory_items:
        canon = _normalize_name(str(it.get("name") or ""))
        base = _item_base_key(canon)
        if len(base) < 3:
            continue
        tokens = [base, *[w for w in base.split() if len(w) >= 4]]
        if any(tok in t for tok in tokens):
            if base not in seen:
                seen.add(base)
                found.append((canon, int(it.get("quantity") or 0)))
    return found


_DRAW_EQUIP_VERBS = (
    "saco ",
    "saco mi",
    "saco la",
    "saco el",
    "sacar",
    "saca ",
    "saca mi",
    "saca la",
    "saca el",
    "empuño",
    "empuñar",
    "empuno",
    "desenvain",
    "equipo ",
    "equipar",
)


def _is_draw_or_equip_intent(player_text: str) -> bool:
    t = (player_text or "").lower().strip()
    if t.startswith("saco") or t.startswith("saca"):
        return True
    return any(v in t for v in _DRAW_EQUIP_VERBS)


def _normalize_action_request_inventory(
    action_req: dict[str, Any],
    *,
    player_text: str,
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Alinea parse LLM con inventario real (nombres difusos, sacar ≠ extremo)."""
    mentioned = _items_mentioned_in_text(player_text, inventory_items)
    draw = _is_draw_or_equip_intent(player_text)
    no_extreme = {"nuclear": False, "transformation": False, "magic_level_undefined": False}

    if action_req.get("action_type") == "extreme" and mentioned:
        action_req["action_type"] = "use_item"
        action_req["extreme_flags"] = dict(no_extreme)

    fixed_items: list[dict[str, Any]] = []
    for it in action_req.get("items_used") or []:
        entry = dict(it)
        name = _normalize_name(str(entry.get("name") or ""))
        resolved = _resolve_inventory_item(name, inventory_items) if name else None
        if resolved:
            entry["name"] = resolved[0]
        if draw or entry.get("equip_only"):
            entry["equip_only"] = True
            entry["declared_quantity"] = 0
        fixed_items.append(entry)
    action_req["items_used"] = fixed_items

    if draw and mentioned and not action_req["items_used"]:
        action_req["action_type"] = "use_item"
        action_req["extreme_flags"] = dict(no_extreme)
        action_req["items_used"] = [
            {"name": mentioned[0][0], "declared_quantity": 0, "equip_only": True}
        ]
    elif action_req.get("action_type") == "unknown" and mentioned and draw:
        action_req["action_type"] = "use_item"
        action_req["items_used"] = [
            {"name": mentioned[0][0], "declared_quantity": 0, "equip_only": True}
        ]

    return action_req


def _extract_action_type_heuristic(player_text: str) -> ActionType:
    t = (player_text or "").lower()
    if any(k in t for k in ("exploro", "investigo", "busco", "rastro", "puerta", "entrap")):
        return "explore"
    if any(k in t for k in ("hablo", "negocio", "persuado", "interrogo", "amenazo")):
        return "talk"
    if any(k in t for k in ("ataq", "disparo", "golpeo", "pego", "acuchillo")):
        return "attack"
    if any(k in t for k in ("maldici", "hechizo", "cast", "lanz", "spell")):
        return "cast_spell"
    if any(
        k in t
        for k in (
            "uso",
            "consumo",
            "tomo",
            "equip",
            "empleo",
            "saco",
            "saca",
            "empuño",
            "empuno",
            "desenvain",
        )
    ):
        return "use_item"
    if any(
        k in t
        for k in (
            "nuclear",
            "bomba",
            "megamento",
            "megazord",
            "transformo",
            "gatling",
            "ametralladora",
            "minigun",
            "bazuca",
            "misil",
            "electron",
            "átomo",
            "atomo",
            "cuántico",
            "quantico",
            "todo lo que existe",
            "toda la realidad",
            "multiverso",
            "omnipotente",
            "omnipotencia",
        )
    ):
        return "extreme"
    return "unknown"


def _heuristic_action_request(player_text: str, *, rules_query: str) -> dict[str, Any]:
    action_type = _extract_action_type_heuristic(player_text)
    extreme = action_type == "extreme"
    return {
        "version": 1,
        "action_type": action_type,
        "intents": [rules_query[:80]],
        "items_used": [],
        "spells_or_skills": [],
        "targets": [],
        "extreme_flags": {
            "nuclear": extreme and "nuclear" in (player_text or "").lower(),
            "transformation": extreme and "transform" in (player_text or "").lower(),
            "magic_level_undefined": extreme,
        },
        "claims": [player_text[:300]],
        "missing_fields": ["llm_parse_fallback"],
        "confidence": 0.2,
    }


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    issues: list[str]


def _validate_action_request(
    action_req: dict[str, Any],
    *,
    inventory_items: list[dict[str, Any]],
    player_state: dict[str, Any],
    world_state: dict[str, Any],
) -> ValidationResult:
    issues: list[str] = []

    ext = action_req.get("extreme_flags") or {}
    if action_req.get("action_type") == "extreme" or any(
        bool(v) for v in ext.values() if isinstance(v, (bool, int))
    ):
        issues.append("extreme_not_supported")
        return ValidationResult(allowed=False, issues=issues)

    inv_map = _inventory_map(inventory_items)
    spells = [s.lower() for s in (player_state.get("spells") or [])]
    skills = [s.lower() for s in (player_state.get("skills") or [])]

    # use_item / equipar (declared_quantity 0 o equip_only = no consumir)
    for it in action_req.get("items_used") or []:
        name = _normalize_name(it.get("name") or "")
        if not name:
            issues.append("empty_item_name")
            continue
        resolved = _resolve_inventory_item(name, inventory_items)
        equip_only = bool(it.get("equip_only"))
        qty_needed = it.get("declared_quantity")
        if equip_only or qty_needed == 0:
            if resolved is None or resolved[1] < 1:
                issues.append(f"missing_inventory_item:{name}")
            continue
        if qty_needed is None:
            qty_needed = 1
        try:
            qty_needed_i = int(qty_needed)
        except (TypeError, ValueError):
            qty_needed_i = 1
        if qty_needed_i < 1:
            qty_needed_i = 1
        avail = resolved[1] if resolved else inv_map.get(name.lower(), 0)
        canon = resolved[0] if resolved else name
        if avail < qty_needed_i:
            issues.append(f"missing_inventory_item:{canon}")

    if action_req.get("action_type") == "cast_spell":
        for sp in action_req.get("spells_or_skills") or []:
            nm = _normalize_name(sp.get("name") or "").lower()
            if not nm:
                continue
            if nm not in spells and nm not in skills:
                issues.append(f"spell_or_skill_not_known:{nm}")

    # attack: require either targets or we mark pending clarification.
    if action_req.get("action_type") == "attack":
        targets = [t for t in (action_req.get("targets") or []) if str(t).strip()]
        if not targets:
            # Si world_state tiene una escena con enemigos conocidos, el parser pudo fallar.
            known = (((world_state or {}).get("scene") or {}) or {}).get("entities") or []
            if not known:
                issues.append("attack_missing_target")

    allowed = len(issues) == 0
    return ValidationResult(allowed=allowed, issues=issues)


def _llm_parse_action_request(
    *,
    player_text: str,
    inventory_items: list[dict[str, Any]],
    player_state: dict[str, Any],
    world_state: dict[str, Any],
    rules_query: str,
) -> dict[str, Any]:
    if settings.llm_provider != "ollama" and not settings.openai_api_key:
        data = _heuristic_action_request(player_text, rules_query=rules_query)
        data["missing_fields"] = ["inventory_or_state_required"]
        data["confidence"] = 0.15
        return data

    system = "Eres un parser estricto de acciones para un motor de rol persistente. Salida JSON exacta."
    user = f"""
{_ACTION_REQUEST_SCHEMA_HINT}

Inventario:
{json.dumps(inventory_items, ensure_ascii=False)[:800]}

Estado jugador (resumen):
{json.dumps(player_state, ensure_ascii=False)[:_PIPELINE_CTX_LIMIT]}

Mundo:
{json.dumps(world_state, ensure_ascii=False)[:_PIPELINE_CTX_LIMIT]}

Texto del jugador:
\"\"\"\n{player_text}\n\"\"\"
"""
    raw = ollama_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options_extra=_pipeline_ollama_options(),
    )
    try:
        data = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        data = _heuristic_action_request(player_text, rules_query=rules_query)
    data.setdefault("missing_fields", [])
    data.setdefault("intents", [])
    data.setdefault("items_used", [])
    data.setdefault("spells_or_skills", [])
    data.setdefault("targets", [])
    data.setdefault(
        "extreme_flags",
        {"nuclear": False, "transformation": False, "magic_level_undefined": False},
    )
    data.setdefault("confidence", 0.5)
    data.setdefault("claims", [player_text[:300]])
    # Normaliza nombres.
    for it in data.get("items_used") or []:
        if it.get("name"):
            it["name"] = _normalize_name(str(it["name"]))
    for sp in data.get("spells_or_skills") or []:
        if sp.get("name"):
            sp["name"] = _normalize_name(str(sp["name"]))
    data["action_type"] = str(data.get("action_type") or "unknown")
    return _normalize_action_request_inventory(
        data, player_text=player_text, inventory_items=inventory_items
    )


def _pipeline_ollama_options() -> dict[str, float | int]:
    opts: dict[str, float | int] = {
        "temperature": settings.dm_pipeline_llm_temperature,
        "num_predict": settings.dm_pipeline_llm_num_predict,
    }
    if settings.dm_pipeline_llm_num_ctx:
        opts["num_ctx"] = int(settings.dm_pipeline_llm_num_ctx)
    return opts


def _fast_action_request(
    player_text: str,
    *,
    rules_query: str,
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any]:
    req = _heuristic_action_request(player_text, rules_query=rules_query)
    return _normalize_action_request_inventory(
        req, player_text=player_text, inventory_items=inventory_items
    )


def _fast_parse_sufficient(action_req: dict[str, Any], player_text: str) -> bool:
    at = str(action_req.get("action_type") or "unknown")
    items = action_req.get("items_used") or []
    if at == "use_item" and items and all(bool(it.get("equip_only")) for it in items):
        return True
    if at in ("explore", "talk", "extreme"):
        return True
    if at == "attack" and any(str(t).strip() for t in action_req.get("targets") or []):
        return True
    if at == "use_item" and items:
        return True
    if at == "cast_spell" and action_req.get("spells_or_skills"):
        return False
    if at == "unknown" and len((player_text or "").split()) > 12:
        return False
    return len(player_text or "") <= 160


def _action_needs_rules_rag(action_req: dict[str, Any]) -> bool:
    at = str(action_req.get("action_type") or "")
    return at in ("cast_spell", "attack") and bool(
        action_req.get("spells_or_skills") or action_req.get("targets")
    )


def _can_resolve_effects_without_llm(
    action_req: dict[str, Any], validation: ValidationResult
) -> bool:
    if not validation.allowed:
        return True
    items = action_req.get("items_used") or []
    if items and all(bool(it.get("equip_only")) for it in items):
        return True
    at = str(action_req.get("action_type") or "")
    if at in ("explore", "talk", "unknown", "attack", "extreme"):
        return True
    if at == "use_item":
        return not any(
            int(it.get("declared_quantity") or 0) > 0
            for it in items
            if not it.get("equip_only")
        )
    if at == "cast_spell":
        return False
    return True


def _resolve_effects_deterministic(
    *,
    action_req: dict[str, Any],
    validation: ValidationResult,
    player_state: dict[str, Any],
    world_state: dict[str, Any],
) -> dict[str, Any] | None:
    items_used = action_req.get("items_used") or []
    equip_only = bool(items_used) and all(bool(it.get("equip_only")) for it in items_used)
    if validation.allowed and equip_only:
        names = ", ".join(
            [_normalize_name(str(it.get("name") or "")) for it in items_used if it.get("name")]
        )
        return {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": False,
            "needs_roll": False,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {
                "scene": f"Preparas o empuñas: {names}.",
                "time": {"tick": 0},
            },
            "narration_facts": [
                f"El personaje saca o empuña {names}, que ya consta en su inventario.",
            ],
            "dm_questions": ["¿Qué haces a continuación (apuntar, moverte, hablar, atacar)?"],
        }

    if not validation.allowed:
        issues = validation.issues
        if "extreme_not_supported" in issues:
            narration = [
                "La acción excede las reglas y el tono de la campaña (tecnología o poder fuera de escena).",
            ]
            question = (
                "¿Qué acción encaja con tu ficha, inventario y el mundo actual "
                "(arma, hechizo o maniobra que puedas justificar en mesa)?"
            )
        elif any(i.startswith("missing_inventory_item:") for i in issues):
            narration = [
                "No consta ese objeto en tu inventario persistente; el motor no puede aplicarlo.",
            ]
            question = "¿Qué objeto o recurso tienes registrado en inventario o ficha para hacer esto?"
        elif "attack_missing_target" in issues:
            narration = ["No hay un objetivo claro en escena para ese ataque."]
            question = "¿A quién o qué atacas exactamente (nombre visible en la escena)?"
        else:
            narration = [
                "El motor rechaza la acción por falta de condiciones obligatorias (inventario/ficha/estado).",
            ]
            question = (
                "¿Qué tienes disponible en tu inventario o en tu ficha para ejecutar "
                "esta acción de forma válida?"
            )
        return {
            "version": 1,
            "allowed": False,
            "block_reason": ",".join(issues),
            "needs_player_clarification": True,
            "needs_roll": False,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {"scene": "Sin cambios aplicados.", "time": {"tick": 0}},
            "narration_facts": narration,
            "dm_questions": [question],
        }

    at = str(action_req.get("action_type") or "unknown")
    scene_hint = ""
    scene_val = (world_state or {}).get("scene")
    if isinstance(scene_val, dict):
        scene_hint = str((scene_val or {}).get("summary") or "")[:120]
    elif isinstance(scene_val, str):
        scene_hint = scene_val[:120]

    if at == "explore":
        return {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": False,
            "needs_roll": False,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {
                "scene": scene_hint or "Exploras el entorno con atención.",
                "time": {"tick": 1},
            },
            "narration_facts": ["Exploras el entorno y observas detalles relevantes."],
            "dm_questions": ["¿Qué buscas o qué zona examinas con más detalle?"],
        }
    if at == "talk":
        return {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": False,
            "needs_roll": False,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {
                "scene": scene_hint or "La escena sigue abierta al diálogo.",
                "time": {"tick": 0},
            },
            "narration_facts": ["Te diriges a quienes están en escena para hablar o negociar."],
            "dm_questions": ["¿Qué les dices exactamente?"],
        }
    if at == "attack":
        targets = ", ".join(
            [str(t) for t in action_req.get("targets") or [] if str(t).strip()][:4]
        )
        tgt = targets or "el objetivo en escena"
        return {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": False,
            "needs_roll": True,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {"scene": f"Te posicionas para atacar a {tgt}.", "time": {"tick": 0}},
            "narration_facts": [f"Preparas un ataque contra {tgt}."],
            "dm_questions": ["Indica la tirada de ataque (y daño si aplica) según tu ficha y las reglas."],
        }
    if at == "unknown":
        return {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": True,
            "needs_roll": False,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {
                "scene": scene_hint or "La escena permanece estable.",
                "time": {"tick": 0},
            },
            "narration_facts": ["Registras la intención; el efecto mecánico queda pendiente de concreción."],
            "dm_questions": ["¿Puedes concretar objetivo, tirada o recurso de ficha/inventario?"],
        }
    return None


def _build_rules_query(action_req: dict[str, Any], player_text: str) -> str:
    at = str(action_req.get("action_type") or "")
    targets = ", ".join([str(t) for t in action_req.get("targets") or [] if str(t).strip()][:6])
    items = ", ".join(
        [str(i.get("name") or "") for i in action_req.get("items_used") or [] if i.get("name")][:6]
    )
    spells = ", ".join(
        [str(s.get("name") or "") for s in action_req.get("spells_or_skills") or [] if s.get("name")][:6]
    )
    return f"acción:{at}; items:{items}; spells:{spells}; targets:{targets}; texto:{player_text[:120]}"


def _llm_resolve_effects(
    *,
    action_req: dict[str, Any],
    validation: ValidationResult,
    player_state: dict[str, Any],
    world_state: dict[str, Any],
    rules_context: dict[str, Any],
) -> dict[str, Any]:
    det = _resolve_effects_deterministic(
        action_req=action_req,
        validation=validation,
        player_state=player_state,
        world_state=world_state,
    )
    if det is not None:
        return det

    if settings.llm_provider != "ollama" and not settings.openai_api_key:
        # fallback: éxito sin números (pero sin inventar).
        return {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": True,
            "needs_roll": True,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {"scene": "El mundo reacciona, pero falta información mecánica para aplicar efectos numéricos.", "time": {"tick": 1}},
            "narration_facts": ["La acción se considera válida en intención, pero requiere datos para aplicar mecánicas."],
            "dm_questions": ["Indica el arma/recursos exactos o la tirada que quieres hacer según tu ficha y las reglas de la mesa."],
        }

    system = "Eres el árbitro de efectos de un motor de rol. Debes respetar validación y estado; salida JSON exacta."
    rules_text = ""
    try:
        # Solo un extracto para que el LLM no invente.
        rules = rules_context.get("rules") or []
        rules_text = "\n".join([r.get("text", "")[:400] for r in rules[:6]])
    except Exception:
        rules_text = ""

    user = f"""
{_ACTION_EFFECTS_SCHEMA_HINT}

ActionRequest:
{json.dumps(action_req, ensure_ascii=False)[:_PIPELINE_CTX_LIMIT]}

PlayerState:
{json.dumps(player_state, ensure_ascii=False)[:1200]}

WorldState:
{json.dumps(world_state, ensure_ascii=False)[:1200]}

Reglas (extracto):
{rules_text[:900]}
"""
    raw = ollama_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options_extra=_pipeline_ollama_options(),
    )
    try:
        data = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError):
        data = {
            "version": 1,
            "allowed": True,
            "block_reason": None,
            "needs_player_clarification": True,
            "needs_roll": True,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {
                "scene": "El mundo reacciona con cautela; faltan datos mecánicos para cerrar el efecto.",
                "time": {"tick": 1},
            },
            "narration_facts": [
                "La intención se registra, pero el motor no pudo resolver efectos numéricos desde el LLM.",
            ],
            "dm_questions": [
                "Indica tirada, objetivo y recursos según tu ficha para que el efecto quede aplicado.",
            ],
        }
    data.setdefault("inventory_consumptions", [])
    data.setdefault("player_state_delta", {"hp": {"current": None, "max": None}, "conditions_add": [], "conditions_remove": []})
    data.setdefault("world_state_delta", {"scene": "", "time": {"tick": 0}})
    data.setdefault("narration_facts", [])
    data.setdefault("dm_questions", [])
    data.setdefault("needs_player_clarification", True)
    data.setdefault("needs_roll", False)
    return data


def _validate_effects(
    effects: dict[str, Any],
    *,
    action_req: dict[str, Any],
    inventory_items: list[dict[str, Any]],
    player_state: dict[str, Any],
) -> ValidationResult:
    issues: list[str] = []

    inv_map = _inventory_map(inventory_items)
    for c in effects.get("inventory_consumptions") or []:
        nm = _normalize_name(str(c.get("item_name") or ""))
        qty = c.get("quantity") or 0
        try:
            qty_i = int(qty)
        except Exception:
            qty_i = 0
        if nm and qty_i > inv_map.get(nm.lower(), 0):
            issues.append(f"inventory_consumption_exceeds:{nm}")

    # Si dice cast_spell, asegura que no aplique cambios de HP si no hay hp info.
    if action_req.get("action_type") == "cast_spell":
        hp = player_state.get("hp") or {}
        if hp.get("max") is None and hp.get("current") is None:
            # no bloqueamos; solo pedimos clarify en efectos
            pass

    allowed = len(issues) == 0
    return ValidationResult(allowed=allowed, issues=issues)


def _apply_effects_to_state(
    *,
    action_req: dict[str, Any],
    effects: dict[str, Any],
    world_state: dict[str, Any],
    player_state: dict[str, Any],
    inventory_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    # v1: aplicamos solo:
    # - world_state.scene si viene explícito
    # - player_state.conditions_add si viene
    # - consumos se aplican sobre inventario (fuera de este scope; lo dejaré para play.py)
    raw_world_delta = effects.get("world_state_delta") or {}
    raw_ps_delta = effects.get("player_state_delta") or {}

    # Normalizamos a “parches” coherentes con WorldState/PlayerState.
    world_patch: dict[str, Any] = {}
    if isinstance(raw_world_delta, dict):
        scene_val = raw_world_delta.get("scene")
        if isinstance(scene_val, str) and scene_val.strip():
            world_patch["scene"] = {"summary": scene_val.strip()}
        elif isinstance(scene_val, dict):
            world_patch["scene"] = scene_val

        time_val = raw_world_delta.get("time") or {}
        if isinstance(time_val, dict) and "tick" in time_val:
            tick = time_val.get("tick")
            try:
                world_patch["time"] = {"tick": int(tick)}
            except Exception:
                pass

    player_patch: dict[str, Any] = {}
    if isinstance(raw_ps_delta, dict):
        base_conditions = list(player_state.get("conditions") or [])
        add = raw_ps_delta.get("conditions_add") or []
        remove = raw_ps_delta.get("conditions_remove") or []
        if isinstance(add, list) or isinstance(remove, list):
            new_conditions = [
                c for c in base_conditions if str(c).strip() not in {str(x).strip() for x in remove}
            ]
            for c in add:
                cc = str(c).strip()
                if cc and cc not in new_conditions:
                    new_conditions.append(cc)
            if new_conditions != base_conditions:
                player_patch["conditions"] = new_conditions

        hp_base = dict(player_state.get("hp") or {})
        hp_delta = raw_ps_delta.get("hp") or {}
        if isinstance(hp_delta, dict):
            cur = hp_delta.get("current")
            mx = hp_delta.get("max")
            changed = False
            if cur is not None and cur == cur:
                try:
                    hp_base["current"] = float(cur)
                    changed = True
                except Exception:
                    pass
            if mx is not None and mx == mx:
                try:
                    hp_base["max"] = float(mx)
                    changed = True
                except Exception:
                    pass
            if changed:
                player_patch["hp"] = hp_base

    return world_patch, player_patch


async def handle_action_pipeline(
    *,
    player_text: str,
    campaign_id: str,
    user_id: str,
    player_state: dict[str, Any],
    world_state: dict[str, Any],
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return await _handle_action_pipeline_impl(
            player_text=player_text,
            campaign_id=campaign_id,
            user_id=user_id,
            player_state=player_state,
            world_state=world_state,
            inventory_items=inventory_items,
        )
    except RuntimeError:
        raise
    except Exception as e:
        logger.exception(
            "pipeline degradado campaign=%s user=%s: %s", campaign_id, user_id, e
        )
        return _degraded_pipeline_result(
            player_text=player_text,
            player_state=player_state,
            world_state=world_state,
            inventory_items=inventory_items,
        )


def _degraded_pipeline_result(
    *,
    player_text: str,
    player_state: dict[str, Any],
    world_state: dict[str, Any],
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any]:
    action_req = _normalize_action_request_inventory(
        _heuristic_action_request(player_text, rules_query=player_text[:400]),
        player_text=player_text,
        inventory_items=inventory_items,
    )
    validation = _validate_action_request(
        action_req,
        inventory_items=inventory_items,
        player_state=player_state,
        world_state=world_state,
    )
    effects = _llm_resolve_effects(
        action_req=action_req,
        validation=validation,
        player_state=player_state,
        world_state=world_state,
        rules_context={"rules": []},
    )
    world_delta, ps_delta = _apply_effects_to_state(
        action_req=action_req,
        effects=effects,
        world_state=world_state,
        player_state=player_state,
        inventory_items=inventory_items,
    )
    return {
        "action_request": action_req,
        "validation": {"allowed": validation.allowed, "issues": validation.issues},
        "effects": effects,
        "world_delta": world_delta,
        "player_delta": ps_delta,
        "pipeline_meta": {"mode": "degraded", "llm_parse": False, "llm_effects": False, "rag": False},
    }


async def _handle_action_pipeline_impl(
    *,
    player_text: str,
    campaign_id: str,
    user_id: str,
    player_state: dict[str, Any],
    world_state: dict[str, Any],
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any]:
    mode = settings.dm_action_pipeline
    rules_query = player_text[:400]
    pipeline_meta: dict[str, Any] = {"mode": mode, "llm_parse": False, "llm_effects": False, "rag": False}

    # 1) Parse (rápido por defecto)
    if mode == "fast":
        action_req = _fast_action_request(
            player_text, rules_query=rules_query, inventory_items=inventory_items
        )
    elif mode == "hybrid":
        fast_req = _fast_action_request(
            player_text, rules_query=rules_query, inventory_items=inventory_items
        )
        if _fast_parse_sufficient(fast_req, player_text):
            action_req = fast_req
        else:
            pipeline_meta["llm_parse"] = True
            action_req = await _to_thread_if_needed(
                _llm_parse_action_request,
                player_text=player_text,
                inventory_items=inventory_items,
                player_state=player_state,
                world_state=world_state,
                rules_query=rules_query,
            )
    else:
        pipeline_meta["llm_parse"] = True
        action_req = await _to_thread_if_needed(
            _llm_parse_action_request,
            player_text=player_text,
            inventory_items=inventory_items,
            player_state=player_state,
            world_state=world_state,
            rules_query=rules_query,
        )

    # 2) Validate determinista
    validation = _validate_action_request(
        action_req,
        inventory_items=inventory_items,
        player_state=player_state,
        world_state=world_state,
    )

    # 3) RAG de reglas (opcional; embeddings son lentos)
    rules_context: dict[str, Any] = {"rules": []}
    if settings.dm_play_rag_on_turn and _action_needs_rules_rag(action_req):
        pipeline_meta["rag"] = True
        rules_items, _rules_trace = await _to_thread_if_needed(
            retrieve_rules,
            campaign_id=campaign_id,
            query=_build_rules_query(action_req, player_text),
            k=min(settings.dm_rag_rules_k, 4),
        )
        rules_context = {"rules": rules_items}

    # 4) Resolve effects (determinista primero; LLM solo si hace falta)
    effects: dict[str, Any] | None = _resolve_effects_deterministic(
        action_req=action_req,
        validation=validation,
        player_state=player_state,
        world_state=world_state,
    )
    if effects is None and (
        mode == "full"
        or (mode == "hybrid" and not _can_resolve_effects_without_llm(action_req, validation))
    ):
        pipeline_meta["llm_effects"] = True
        effects = await _to_thread_if_needed(
            _llm_resolve_effects,
            action_req=action_req,
            validation=validation,
            player_state=player_state,
            world_state=world_state,
            rules_context=rules_context,
        )
    elif effects is None:
        effects = _resolve_effects_deterministic(
            action_req=action_req,
            validation=validation,
            player_state=player_state,
            world_state=world_state,
        ) or {
            "version": 1,
            "allowed": validation.allowed,
            "block_reason": None if validation.allowed else ",".join(validation.issues),
            "needs_player_clarification": True,
            "needs_roll": False,
            "inventory_consumptions": [],
            "player_state_delta": {
                "hp": {"current": None, "max": None},
                "conditions_add": [],
                "conditions_remove": [],
            },
            "world_state_delta": {"scene": "", "time": {"tick": 0}},
            "narration_facts": ["La acción queda registrada."],
            "dm_questions": ["¿Qué detalle mecánico o narrativo quieres fijar?"],
        }

    # 5) Revalidate effects
    eff_val = _validate_effects(
        effects,
        action_req=action_req,
        inventory_items=inventory_items,
        player_state=player_state,
    )
    if not eff_val.allowed:
        effects = {
            **effects,
            "allowed": False,
            "block_reason": ",".join(eff_val.issues),
            "needs_player_clarification": True,
            "inventory_consumptions": [],
            "narration_facts": [
                "La acción se rechaza porque implicaba consumos inválidos según tu inventario persistente.",
            ],
            "dm_questions": [
                "Indica el item correcto y su cantidad disponible en tu inventario.",
            ],
        }

    # 6) Apply (deltas)
    world_delta, ps_delta = _apply_effects_to_state(
        action_req=action_req,
        effects=effects,
        world_state=world_state,
        player_state=player_state,
        inventory_items=inventory_items,
    )

    return {
        "action_request": action_req,
        "validation": {"allowed": validation.allowed, "issues": validation.issues},
        "effects": effects,
        "world_delta": world_delta,
        "player_delta": ps_delta,
        "pipeline_meta": pipeline_meta,
    }


async def _to_thread_if_needed(fn, /, *args: Any, **kwargs: Any) -> Any:
    # Mantiene el event loop libre.
    return await asyncio.to_thread(fn, *args, **kwargs)

