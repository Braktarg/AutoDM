from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

engine = create_async_engine(
    settings.database_url,
    echo=False,
)
async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def _sqlite_migrate(conn) -> None:
    r = await conn.execute(text("PRAGMA table_info(campaigns)"))
    cols = {row[1] for row in r.fetchall()}
    alters: list[str] = []
    if "description" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN description TEXT")
    if "game_system" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN game_system VARCHAR(80)")
    if "narrative_mode" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN narrative_mode VARCHAR(32)")
    if "status" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN status VARCHAR(32) DEFAULT 'lobby'")
    if "cover_image" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN cover_image VARCHAR(255)")
    if "session_summary" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN session_summary TEXT")
    if "dm_prompt_addon" not in cols:
        alters.append("ALTER TABLE campaigns ADD COLUMN dm_prompt_addon TEXT")
    for sql in alters:
        await conn.execute(text(sql))
    await conn.execute(
        text("UPDATE campaigns SET status = 'lobby' WHERE status IS NULL")
    )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in settings.database_url:
            await _sqlite_migrate(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
