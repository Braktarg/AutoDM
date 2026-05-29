from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, campaigns, documents, graph, inventory, play
from app.config import settings
from app.db.session import init_db
from app.services.world_scheduler import world_tick_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    task = asyncio.create_task(world_tick_loop(stop))
    try:
        yield
    finally:
        stop.set()
        if not task.done():
            task.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(campaigns.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(play.router, prefix="/api")
app.include_router(inventory.router, prefix="/api")
app.include_router(graph.router, prefix="/api")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
