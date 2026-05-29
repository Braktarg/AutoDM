# AutoDM — AI Dungeon Master

Mesa de rol multijugador con **Dungeon Master impulsado por IA**, **RAG sobre documentos propios** (reglas, manuales, fichas PDF) y **GraphRAG** por campaña. Cada mesa es un entorno aislado: inventario persistente, estado del mundo, validación de acciones y memoria narrativa.

Proyecto personal full-stack: diseño, backend, frontend e integración LLM.

> **Licencia:** código propietario — reservado para explotación comercial (SaaS). Publicado en GitHub como portfolio; no está autorizado el uso comercial, redistribución ni forks para productos derivados sin permiso escrito.

## Demo rápida (qué hace)

- Crear campañas (D&D 5e u otro sistema), subir PDFs de reglas y fichas.
- Jugar en tiempo casi real (SSE): el jugador escribe acciones; el DM responde con coherencia respecto a inventario, ficha y reglas indexadas.
- **Pipeline persistente**: parse de acción → validación (inventario / hechizos / ICP de mundo) → efectos → narración determinista (sin que el LLM invente mecánicas).
- **RAG híbrido** (Chroma + re-ranking lexical) por campaña.
- **GraphRAG** local (JSON) o Neo4j opcional; menciones estilo Obsidian `[[NPC]]` / `#lugar` en el chat.
- Modo **rápido** (`DM_PACE=fast`): turnos comunes sin llamadas LLM (~ms).

## Stack

| Capa | Tecnología |
|------|------------|
| Frontend | React 19, TanStack Router/Start, Vite, Tailwind, React Query |
| Backend | Python 3.11+, FastAPI, SQLAlchemy (async), SQLite |
| IA | Ollama (local) u OpenAI; embeddings + chat |
| RAG | ChromaDB persistente, chunking PDF, re-ranking |
| Grafo | JSON por campaña o Neo4j |
| Auth | JWT, bcrypt |

## Requisitos

- **Python 3.11+**
- **Node.js 20+**
- **Ollama** (recomendado) con modelos de chat y embeddings, o **OpenAI API key**

## Instalación local

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edita .env si usas OpenAI o Neo4j
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. Frontend

```powershell
cd frontend
copy .env.example .env
npm install
npm run dev
```

Abre la URL que indique Vite (p. ej. `http://127.0.0.1:5173`). El frontend usa `VITE_API_URL=http://127.0.0.1:8000` por defecto.

### 3. Ollama (opcional pero recomendado)

```bash
ollama serve
ollama pull gemma4:e4b
ollama pull nomic-embed-text
```

### 4. Neo4j (opcional)

```bash
docker compose up -d neo4j
```

En `backend/.env`: `USE_NEO4J=true` y credenciales `NEO4J_*`.

## Variables de entorno clave

Ver `backend/.env.example` y `frontend/.env.example`.

| Variable | Descripción |
|----------|-------------|
| `LLM_PROVIDER` | `ollama` o `openai` |
| `DM_PACE` | `fast` \| `balanced` \| `cinematic` |
| `DM_ACTION_PIPELINE` | `fast` \| `hybrid` \| `full` — control de LLM por turno |
| `DM_PLAY_RAG_ON_TURN` | RAG en cada acción (más lento si `true`) |

## Arquitectura

```
frontend/          React + TanStack (UI mesa, lobby, reglas, personaje)
backend/app/
  api/             REST + SSE (auth, campañas, play, documentos, grafo)
  services/        RAG, DM engine, action pipeline, GraphRAG, memoria
  db/              Modelos SQLAlchemy
backend/data/      SQLite, Chroma, uploads, grafos (no versionado)
```

## Autor

**Eduardo Andrés Troncoso Sáez** — [GitHub @Braktarg](https://github.com/Braktarg)

UI base inspirada en [ai-dungeon-master](https://github.com/Braktarg/ai-dungeon-master); motor RAG, pipeline persistente y GraphRAG desarrollados para este repo.

## Licencia

**Copyright © 2026 Eduardo Andrés Troncoso Sáez. Todos los derechos reservados.**

Ver [LICENSE](./LICENSE). El código no es open source: está visible para evaluación técnica (p. ej. portfolio, procesos de selección). Uso comercial, hosting como servicio o derivados requieren acuerdo con el autor.
