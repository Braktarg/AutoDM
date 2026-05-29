"""Pub/sub de chat: memoria local + Redis opcional (Sprint 5)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config import settings

_listeners: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
_redis_tasks: dict[asyncio.Queue[dict[str, Any]], asyncio.Task] = {}


async def _redis_listener(campaign_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    try:
        import redis.asyncio as redis
    except Exception:
        return
    if not settings.redis_url:
        return
    client = redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    channel = f"autodm:chat:{campaign_id}"
    await pubsub.subscribe(channel)
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not msg or msg.get("type") != "message":
                await asyncio.sleep(0.05)
                continue
            raw = msg.get("data") or ""
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await client.close()
        except Exception:
            pass


def subscribe(campaign_id: str) -> asyncio.Queue[dict[str, Any]]:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
    _listeners.setdefault(campaign_id, set()).add(q)
    if settings.redis_url:
        _redis_tasks[q] = asyncio.create_task(_redis_listener(campaign_id, q))
    return q


def unsubscribe(campaign_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
    subs = _listeners.get(campaign_id)
    if not subs:
        return
    subs.discard(q)
    if not subs:
        _listeners.pop(campaign_id, None)
    t = _redis_tasks.pop(q, None)
    if t and not t.done():
        t.cancel()


def _notify_campaign_payload(campaign_id: str, payload: dict[str, Any]) -> None:
    subs = list(_listeners.get(campaign_id, ()))
    for q in subs:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
    if settings.redis_url:
        asyncio.create_task(_publish_redis(campaign_id, payload))


def notify_chat_updated(campaign_id: str) -> None:
    _notify_campaign_payload(
        campaign_id,
        {"type": "messages_updated", "campaign_id": campaign_id},
    )


def notify_inventory_updated(campaign_id: str) -> None:
    _notify_campaign_payload(
        campaign_id,
        {"type": "inventory_updated", "campaign_id": campaign_id},
    )


async def _publish_redis(campaign_id: str, payload: dict[str, Any]) -> None:
    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis_url or "", decode_responses=True)
        await client.publish(f"autodm:chat:{campaign_id}", json.dumps(payload))
        await client.close()
    except Exception:
        # fallback local ya cubre funcionamiento en single-process.
        pass
