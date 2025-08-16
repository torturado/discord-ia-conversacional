from __future__ import annotations

import asyncio
import logging
import random
from typing import Dict, Tuple, List, Optional

import discord
from discord.ext import commands

from .config import settings
from .gemini_client import generate_reply


logger = logging.getLogger(__name__)


# discord.py-self no necesita intents explícitos para selfbots
# Los selfbots tienen acceso a todos los eventos por defecto
bot = commands.Bot(command_prefix="!", self_bot=True)

# Cooldown dictionary: key=(channel_id, author_id) -> last_reply_time
cooldowns: Dict[Tuple[int, int], float] = {}
# Pacing por canal para evitar 429
last_send_by_channel: Dict[int, float] = {}
# Colas y workers por canal
send_queues: Dict[int, asyncio.Queue] = {}
send_workers: Dict[int, asyncio.Task] = {}

# Seguimiento de actividad (último trigger por canal)
last_trigger_by_channel: Dict[int, float] = {}
inactivity_task: Optional[asyncio.Task] = None

# Memoria por canal: guarda hasta 20 últimos intercambios donde nos mencionan
# Estructura: channel_id -> List[{"role": "user"|"model", "text": str}]
memory_by_channel: Dict[int, List[Dict[str, str]]] = {}


async def _simulate_human_typing(text: str, channel) -> None:
    """Simula comportamiento humano: pausa para 'pensar', luego typing indicator."""
    if not settings.HUMAN_SIMULATION_ENABLED:
        return
    # Tiempo inicial de "pensamiento" (más variable)
    thinking_time = random.uniform(settings.MIN_TYPING_DELAY, min(settings.MAX_TYPING_DELAY, settings.TYPING_MAX_SECONDS_CAP))
    # Breve pre-pausa
    await asyncio.sleep(thinking_time)
    # Tiempo de "escritura" basado en longitud del texto, cap pequeño para fluidez
    word_count = max(1, len(text.split()))
    typing_time = min(max(0.4, word_count / settings.WORDS_PER_SECOND), settings.TYPING_MAX_SECONDS_CAP)
    # Variación natural
    typing_time *= random.uniform(0.85, 1.15)
    async with channel.typing():
        await asyncio.sleep(typing_time)


def _ensure_send_worker(channel_id: int) -> None:
    if channel_id in send_queues:
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=settings.QUEUE_MAX_SIZE_PER_CHANNEL)
    send_queues[channel_id] = queue

    async def _worker() -> None:
        while True:
            # Coalesce window: agrupar items cercanos para no saltarnos mensajes
            item = await queue.get()
            try:
                reply_to: Optional[discord.Message] = item.get("reply_to")
                content: str = item["content"]
                channel = reply_to.channel if reply_to is not None else item.get("channel")
                if channel is None:
                    continue
                # Pequeña ventana para coalescer si hay más items pendientes
                coalesced = [content]
                try:
                    with asyncio.timeout(settings.COALESCE_WINDOW_SECONDS):
                        while True:
                            nxt = queue.get_nowait()
                            coalesced.append(nxt["content"])
                            queue.task_done()
                except Exception:
                    pass
                content = "\n".join(coalesced)

                # Simular comportamiento humano
                await _simulate_human_typing(content, channel)

                # Pacing por canal antes de enviar
                now_send = asyncio.get_running_loop().time()
                last_send = last_send_by_channel.get(channel.id, 0.0)
                min_gap = settings.MIN_SECONDS_BETWEEN_MESSAGES_PER_CHANNEL
                jitter = random.uniform(0.0, settings.SEND_JITTER_SECONDS)
                wait_needed = (last_send + min_gap) - now_send
                if wait_needed > 0:
                    await asyncio.sleep(wait_needed + jitter)

                if reply_to is not None:
                    await reply_to.reply(content, mention_author=False)
                else:
                    await channel.send(content)
                last_send_by_channel[channel.id] = asyncio.get_running_loop().time()
            except Exception as e:  # noqa: BLE001
                logger.error("Send worker error", exc_info=e)
            finally:
                queue.task_done()

    send_workers[channel_id] = asyncio.create_task(_worker())


def _enqueue_send(message: discord.Message, content: str) -> None:
    _ensure_send_worker(message.channel.id)
    send_queues[message.channel.id].put_nowait({
        "reply_to": message,
        "content": content,
    })


def _enqueue_send_channel(channel: discord.abc.Messageable, content: str) -> None:
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return
    _ensure_send_worker(channel_id)
    send_queues[channel_id].put_nowait({
        "channel": channel,
        "content": content,
    })


async def _inactivity_watcher() -> None:
    check_interval = 30.0
    while True:
        await asyncio.sleep(check_interval)
        if not settings.INACTIVITY_ENABLED:
            continue
        now = asyncio.get_running_loop().time()
        for channel_id, last_ts in list(last_trigger_by_channel.items()):
            if now - last_ts < settings.INACTIVITY_SECONDS:
                continue
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)  # type: ignore[attr-defined]
                except Exception:
                    continue
            try:
                history = memory_by_channel.get(channel_id, [])[-6:]
                inactivity_prompt = (
                    "inactividad: envía un saludo breve (1–2 líneas), natural y amistoso para reactivar la conversación. "
                    "No menciones que eres IA ni expliques el motivo."
                )
                text = await generate_reply(inactivity_prompt, settings.DISCORD_SYSTEM_PROMPT, history=history)
                text = _format_compact(text, max_lines=2, max_chars=220)
            except Exception:
                continue
            if not text:
                continue
            _enqueue_send_channel(channel, text)
            last_trigger_by_channel[channel_id] = now


def _enqueue_send_channel(channel: discord.abc.Messageable, content: str) -> None:
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return
    _ensure_send_worker(channel_id)
    send_queues[channel_id].put_nowait({
        "channel": channel,
        "content": content,
    })


async def _inactivity_watcher() -> None:
    check_interval = 30.0
    while True:
        await asyncio.sleep(check_interval)
        if not settings.INACTIVITY_ENABLED:
            continue
        now = asyncio.get_running_loop().time()
        for channel_id, last_ts in list(last_trigger_by_channel.items()):
            if now - last_ts < settings.INACTIVITY_SECONDS:
                continue
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)  # type: ignore[attr-defined]
                except Exception:
                    continue
            try:
                history = memory_by_channel.get(channel_id, [])[-6:]
                inactivity_prompt = (
                    "inactividad: envía un saludo breve (1–2 líneas), natural y amistoso para reactivar la conversación. "
                    "No menciones que eres IA ni expliques el motivo."
                )
                msg = await generate_reply(inactivity_prompt, settings.DISCORD_SYSTEM_PROMPT, history=history)
                msg = _format_compact(msg, max_lines=2, max_chars=220)
            except Exception:
                continue
            if not msg:
                continue
            _enqueue_send_channel(channel, msg)
            last_trigger_by_channel[channel_id] = now


def _format_compact(text: str, max_lines: int = 2, max_chars: int = 220) -> str:
    # colapsa espacios, corta a 2 líneas y 220 chars máximo
    if not text:
        return ""
    # normaliza saltos de línea múltiples
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return ""
    text = " ".join(lines)  # empieza compacto en 1 línea
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    # intenta partir en 1–2 líneas naturales (por puntuación)
    # heurística: divide por ". " si existe
    parts = []
    for token in text.replace("¿", "").replace("¡", "").split(". "):
        if token:
            parts.append(token.strip())
        if len(parts) >= max_lines:
            break
    compact = ". ".join(parts)
    # si quedó 1 línea y aún largo, intenta partir por "; " o ", "
    if compact == text and max_lines > 1 and len(text) > max_chars // 2:
        for sep in ["; ", ", "]:
            if sep in text:
                a, _, b = text.partition(sep)
                compact = (a.strip() + "\n" + b.strip())[:max_chars]
                break
    return compact.strip()


def _should_trigger(message: discord.Message) -> bool:
    if message.author == bot.user:
        return False

    # Trigger on direct mention
    try:
        if bot.user and any(user.id == bot.user.id for user in message.mentions):
            return True
    except Exception:
        pass

    # Trigger if message is a reply to us
    ref = getattr(message, "reference", None)
    if ref and ref.message_id:
        # Try to use resolved if available to reduce HTTP
        resolved = getattr(ref, "resolved", None)
        if resolved is not None:
            return bot.user is not None and resolved.author.id == bot.user.id
        # Fallback to fetch
        return asyncio.create_task(_is_reply_to_us(message)) is not None

    return False


async def _is_reply_to_us(message: discord.Message) -> bool:
    ref = message.reference
    if not ref or not ref.message_id:
        return False
    try:
        ref_msg = await message.channel.fetch_message(ref.message_id)
        return bot.user is not None and ref_msg.author.id == bot.user.id
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to fetch referenced message: %s", e)
        return False


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)
    global inactivity_task
    if inactivity_task is None:
        inactivity_task = asyncio.create_task(_inactivity_watcher())
    # Inicializa seguimiento de inactividad para canales permitidos
    try:
        now = asyncio.get_running_loop().time()
        allowed_channels = settings.get_allowed_channel_ids()
        if allowed_channels:
            for cid in allowed_channels:
                last_trigger_by_channel.setdefault(cid, now)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to seed inactivity channels: %s", e)


def _is_allowed_location(message: discord.Message) -> bool:
    """Check if message is from allowed guild/channel based on config."""
    allowed_guilds = settings.get_allowed_guild_ids()
    allowed_channels = settings.get_allowed_channel_ids()
    
    # Si no hay filtros configurados, permitir todos
    if not allowed_guilds and not allowed_channels:
        return True
    
    # Verificar guild si está configurado
    if allowed_guilds:
        guild_id = message.guild.id if message.guild else None
        if guild_id not in allowed_guilds:
            return False
    
    # Verificar canal si está configurado
    if allowed_channels:
        if message.channel.id not in allowed_channels:
            return False
    
    return True


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author == bot.user:
        return
    
    # Filtrar por canal/servidor permitido
    if not _is_allowed_location(message):
        return

    trigger = False
    # Fast path: mention
    if bot.user and any(u.id == bot.user.id for u in message.mentions):
        trigger = True
    else:
        # Potential path: reply to us
        ref = getattr(message, "reference", None)
        if ref and ref.message_id:
            if getattr(ref, "resolved", None) is not None:
                trigger = bot.user is not None and ref.resolved.author.id == bot.user.id
            else:
                trigger = await _is_reply_to_us(message)

    if not trigger:
        return

    now = asyncio.get_running_loop().time()
    key = (message.channel.id, message.author.id)
    last = cooldowns.get(key, 0.0)
    if now - last < settings.REPLY_COOLDOWN_SECONDS:
        return
    cooldowns[key] = now
    last_trigger_by_channel[message.channel.id] = now

    content = message.content or ""
    # Construir historial solo con las últimas 20 entradas para este canal
    history = memory_by_channel.get(message.channel.id, [])[-20:]

    try:
        reply_text = await generate_reply(
            content,
            settings.DISCORD_SYSTEM_PROMPT,
            history=history,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Gemini generation failed", exc_info=e)
        return

    # fuerza 1–2 líneas/220 chars
    reply_text = _format_compact(reply_text, max_lines=2, max_chars=220)

    # Discord 2000 char limit (aquí ya debería ser corto, pero por si acaso)
    if not reply_text:
        return

    chunks = [reply_text[i : i + 2000] for i in range(0, len(reply_text), 2000)]
    for chunk in chunks:
        _enqueue_send(message, chunk)

    # Actualizar memoria: añadimos user input y nuestra respuesta (solo una vez)
    channel_mem = memory_by_channel.setdefault(message.channel.id, [])
    channel_mem.append({"role": "user", "text": content})
    channel_mem.append({"role": "model", "text": reply_text})
    if len(channel_mem) > 20:
        memory_by_channel[message.channel.id] = channel_mem[-20:]


