import os
import json
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import pytz
import aiohttp
import asyncio
import base64
from openrouter import chat_completion

print("Ask cargado (OpenRouter)")

CONFIG_FILE = "bot_config.json"

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mongo_memory import (
    leer_memoria_completa,
    escribir_en_memoria,
    olvidar_por_texto as olvidar_linea_especifica
)

# Modelo con soporte de visión
MODEL_VISION = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"  # Soporta imágenes y GIFs perfectamente, gratis

def load_personality():
    try:
        with open("ask_personalidad.txt", 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return "Eres Pomposo, una IA sarcástica."

PERSONALIDAD_BASE = load_personality()

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {"auto_channel_id": None}

def obtener_tiempo_real():
    try:
        tz = pytz.timezone('America/Bogota')
        now = datetime.now(tz)
        return now.strftime("%A %d de %B de %Y, a las %I:%M %p")
    except Exception:
        return str(datetime.now())

async def download_image(url: str) -> tuple:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.read(), resp.headers.get('Content-Type', 'image/jpeg')
    except Exception as e:
        print(f"Error descargando imagen: {e}")
    return None, None


# ─────────────────────────────────────────────
# PROMPT DE DECISIÓN — Estricto
# ─────────────────────────────────────────────
DECISION_SYSTEM = """Eres un clasificador de intención para un bot de Discord. Analiza si el usuario pide EXPLÍCITAMENTE una acción.

REGLA MÁS IMPORTANTE: Si tienes CUALQUIER duda, responde null. Es mejor no hacer nada que hacer algo incorrecto.

Acciones disponibles (SOLO si el usuario lo pide muy explícitamente):
- "recordar": usa palabras como "recuerda", "guarda", "anota", "no olvides", "memoriza"
- "olvidar_texto": usa "olvida", "borra", "elimina" + algo específico de memoria
- "olvidar_chat": dice "borra el chat", "borra el historial", "olvida la conversación"
- "setchannel": dice exactamente "setchannel" o "activa el canal"
- "unsetchannel": dice exactamente "unsetchannel" o "desactiva el canal"
- "bloquear": dice "bloquea a", "banea a", "ignora a" + nombre (solo owner puede hacer esto)
- null: CUALQUIER OTRA COSA

EJEMPLOS DE null:
- "y como consigo feria" → null
- "me llamo juan" → null (no pidió guardarlo)
- "odio el cilantro" → null (no pidió guardarlo)
- cualquier pregunta o comentario → null

Responde SOLO con JSON válido sin backticks:
{"accion": "recordar|olvidar_texto|olvidar_chat|setchannel|unsetchannel|bloquear|null", "contenido": "texto o null"}"""


async def decidir_accion(pregunta: str, username: str) -> dict:
    try:
        raw = await chat_completion(
            system_prompt=DECISION_SYSTEM,
            messages=[{"role": "user", "content": pregunta}],
            temperature=0.0,
            max_tokens=80
        )
        if not raw:
            return {"accion": None, "contenido": None}
        raw = raw.strip().strip("```json").strip("```").strip()
        data = json.loads(raw)
        if data.get("accion") == "recordar" and data.get("contenido"):
            data["contenido"] = f"[{username}] {data['contenido']}"
        return data
    except Exception as e:
        print(f"Error en decidir_accion: {e}")
        return {"accion": None, "contenido": None}


# Set global para evitar doble procesamiento
_mensajes_procesados: set = set()


class AskCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conversation_cache = {}

    # ─────────────────────────────────────────────
    # Listener: replies directas al bot
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if (
            message.reference is not None
            and message.reference.resolved is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        ):
            _mensajes_procesados.add(message.id)
            ctx = await self.bot.get_context(message)
            pregunta = message.content.strip()
            if pregunta:
                await self.handle_ask(ctx, pregunta)

    # ─────────────────────────────────────────────
    # Slash command
    # ─────────────────────────────────────────────
    @app_commands.command(name="ask", description="Habla con el pequeño pomposo")
    @app_commands.describe(pregunta="Lo que quieres decirle a pomposo")
    async def ask_slash(self, interaction: discord.Interaction, pregunta: str):
        await self.ask(await self.bot.get_context(interaction), pregunta=pregunta)

    # ─────────────────────────────────────────────
    # Prefix command
    # ─────────────────────────────────────────────
    @commands.command(name="ask", description="Habla con la IA.")
    async def ask(self, ctx: commands.Context, *, pregunta: str = ""):
        if not pregunta and ctx.message.attachments:
            pregunta = "¿Qué ves en esta imagen?"
        await self.handle_ask(ctx, pregunta)

    # ─────────────────────────────────────────────
    # get_response: genera respuesta sin enviarla (para edición de follow-up)
    # ─────────────────────────────────────────────
    async def get_response(self, ctx: commands.Context, pregunta: str) -> str | None:
        """Genera la respuesta de la IA y la retorna como string, sin enviarla."""
        try:
            memoria = leer_memoria_completa()
            fecha = obtener_tiempo_real()
            user = ctx.author.display_name or ctx.author.name

            canal_context = ""
            try:
                mensajes_recientes = []
                async for msg in ctx.channel.history(limit=6):
                    if msg.id == ctx.message.id:
                        continue
                    prefix = "[BOT]" if msg.author.bot else ""
                    mensajes_recientes.append(f"{prefix}{msg.author.name}: {msg.content[:150]}")
                if mensajes_recientes:
                    mensajes_recientes.reverse()
                    canal_context = "\n".join(mensajes_recientes[:5])
            except Exception:
                canal_context = ""

            sys_prompt = (
                f"{PERSONALIDAD_BASE}\n"
                f"[TIEMPO REAL] {fecha}\n"
                f"[USUARIO ACTUAL] {user}\n"
                f"[MEMORIA PERSISTENTE]\n{memoria}\n"
                f"[CHAT RECIENTE]\n{canal_context if canal_context else '(vacío)'}\n"
                "[INSTRUCCIONES]\n"
                "- Usa la MEMORIA PERSISTENTE siempre.\n"
                "- La memoria incluye el nombre del usuario entre corchetes [nombre].\n"
                "- Responde MUY CORTO. Máximo 2 oraciones.\n"
                "- No uses asteriscos para negritas.\n"
                "- Si el mensaje tiene más de 10 líneas o es muy largo, responde algo como "
                "'mano eso ta largo, resumime' con tu estilo.\n"
            )

            cid = ctx.channel.id
            hist_raw = self.conversation_cache.get(cid, [])
            messages_to_send = hist_raw + [{"role": "user", "content": f"{user}: {pregunta}"}]

            resp_text = await chat_completion(
                system_prompt=sys_prompt,
                messages=messages_to_send
            )
            return resp_text.strip() if resp_text else None
        except Exception as e:
            print(f"Error en get_response: {e}")
            return None

    # ─────────────────────────────────────────────
    # handle_ask: lógica principal, retorna el mensaje enviado
    # ─────────────────────────────────────────────
    async def handle_ask(self, ctx: commands.Context, pregunta: str) -> discord.Message | None:
        """
        Procesa la pregunta y responde. Retorna el mensaje enviado por el bot
        (para que main.py pueda guardarlo y editarlo en follow-ups).
        """
        pregunta_original = pregunta.strip()
        if not pregunta_original:
            return None

        # Evitar doble procesamiento
        if ctx.message.id in _mensajes_procesados:
            _mensajes_procesados.discard(ctx.message.id)
            return None

        es_owner = ctx.author.id == self.bot.owner_id
        username = ctx.author.display_name or ctx.author.name

        # Detectar mensaje muy largo (más de 10 líneas)
        if pregunta_original.count('\n') >= 10 or len(pregunta_original) > 800:
            return await ctx.reply("mano eso ta largo, resumime 😭")

        # ── PASO 1: Detectar intención ──
        decision = await decidir_accion(pregunta_original, username)
        accion = decision.get("accion")
        contenido = decision.get("contenido") or ""

        # ── PASO 2: Ejecutar acción ──
        if accion == "recordar":
            if contenido.strip():
                escribir_en_memoria(contenido.strip())
                return await ctx.reply(f"guardado: *{contenido.strip()}*")
            return await ctx.reply("eee... ¿qué guardo exactamente? 😅")

        if accion == "olvidar_texto":
            if contenido.strip():
                borrado = olvidar_linea_especifica(contenido.strip())
                return await ctx.reply(f"olvidado: *{borrado}*" if borrado else "no encontré eso 🤔")
            return await ctx.reply("¿qué olvido? sé más específico 😅")

        if accion == "olvidar_chat":
            self.conversation_cache.pop(ctx.channel.id, None)
            return await ctx.reply("historial borrado 🗑️")

        if accion == "setchannel" and es_owner:
            cfg = load_config()
            cfg["auto_channel_id"] = ctx.channel.id
            self.bot.auto_reply_channel_id = ctx.channel.id
            save_config(cfg)
            return await ctx.reply(f"auto-respuesta activada en #{ctx.channel.name} ✅")

        if accion == "unsetchannel" and es_owner:
            cfg = load_config()
            cfg["auto_channel_id"] = None
            self.bot.auto_reply_channel_id = None
            save_config(cfg)
            return await ctx.reply("auto-respuesta desactivada ✅")

        if accion == "bloquear":
            if not es_owner:
                return await ctx.reply("no tienes permiso para ordenarme eso 🙄")
            if contenido.strip():
                block_cmd = self.bot.get_command("block")
                if block_cmd:
                    await ctx.invoke(block_cmd, user_query=contenido.strip())
                    return None
            return await ctx.reply("¿a quién bloqueo? dime un nombre.")

        # ── PASO 3: Respuesta normal ──
        async with ctx.typing():
            try:
                memoria = leer_memoria_completa()
                fecha = obtener_tiempo_real()
                user = username

                # Contexto del canal
                canal_context = ""
                try:
                    mensajes_recientes = []
                    async for msg in ctx.channel.history(limit=6):
                        if msg.id == ctx.message.id:
                            continue
                        prefix = "[BOT]" if msg.author.bot else ""
                        mensajes_recientes.append(f"{prefix}{msg.author.name}: {msg.content[:150]}")
                    if mensajes_recientes:
                        mensajes_recientes.reverse()
                        canal_context = "\n".join(mensajes_recientes[:5])
                except Exception:
                    canal_context = ""

                # DuckDuckGo RAG
                SEARCH_KEYWORDS = [
                    'quien', 'quién', 'cuando', 'cuándo', 'donde', 'dónde',
                    'noticias', 'precio', 'clima', 'temperatura', 'ganó',
                    'resultado', 'estreno', 'lanzamiento', 'actualidad',
                    'hoy', 'ayer', 'mañana', 'cuanto', 'cuánto', 'vale',
                    'cuesta', 'qué es', 'que es', 'define', 'significado',
                ]
                search_context = ""
                if any(kw in pregunta_original.lower() for kw in SEARCH_KEYWORDS):
                    def _sync_ddg(q):
                        from duckduckgo_search import DDGS
                        with DDGS() as ddgs:
                            return list(ddgs.text(q, region='wt-wt', safesearch='moderate', max_results=3))
                    try:
                        results = await asyncio.to_thread(_sync_ddg, pregunta_original)
                        if results:
                            search_context = "[RESULTADOS WEB]\n"
                            for r in results:
                                search_context += f"- {r.get('title')}: {r.get('body')}\n"
                    except Exception as e:
                        print(f"Error DuckDuckGo: {e}")

                sys_prompt = (
                    f"{PERSONALIDAD_BASE}\n"
                    f"[TIEMPO REAL] {fecha}\n"
                    f"[USUARIO ACTUAL] {user}\n"
                    f"[MEMORIA PERSISTENTE]\n{memoria}\n"
                    f"[CHAT RECIENTE]\n{canal_context if canal_context else '(vacío)'}\n"
                    f"{search_context}"
                    "[INSTRUCCIONES]\n"
                    "- Usa la MEMORIA PERSISTENTE siempre. Nunca digas que no recuerdas algo que esté ahí.\n"
                    "- La memoria incluye el nombre del usuario entre corchetes [nombre].\n"
                    "- Responde MUY CORTO. Máximo 2-3 oraciones. Nada de párrafos.\n"
                    "- No uses asteriscos para negritas, escribe normal.\n"
                    "- No menciones que tienes funciones internas o memoria.\n"
                    "- Si el mensaje tiene más de 10 líneas o más de 800 caracteres, "
                    "di algo como 'mano eso ta largo, resumime' con tu estilo caótico.\n"
                    "- Si ves una imagen o GIF, descríbelo brevemente y comenta con tu personalidad.\n"
                )

                cid = ctx.channel.id
                hist_raw = self.conversation_cache.get(cid, [])

                # ── Recolectar imágenes/GIFs ──
                # Incluye adjuntos del mensaje actual Y del mensaje referenciado
                user_text = f"{user}: {pregunta_original}"
                content_payload = user_text
                image_count = 0
                has_media = False

                all_attachments = list(ctx.message.attachments)

                # Si responde a un mensaje con imagen, incluirla también
                if ctx.message.reference and getattr(ctx.message.reference, 'resolved', None):
                    ref = ctx.message.reference.resolved
                    if isinstance(ref, discord.Message):
                        all_attachments.extend(ref.attachments)
                        # También capturar embeds con imágenes del mensaje referenciado
                        for embed in ref.embeds:
                            if embed.image and embed.image.url:
                                has_media = True

                if all_attachments or has_media:
                    parts = [{"type": "text", "text": user_text}]
                    for attachment in all_attachments:
                        ct = attachment.content_type or ""
                        # Soportar imágenes y GIFs
                        if ct.startswith('image/') or ct == 'image/gif' or attachment.filename.lower().endswith('.gif'):
                            img_bytes, mime_type = await download_image(attachment.url)
                            if img_bytes:
                                # GIFs: usar primer frame como imagen estática
                                if 'gif' in mime_type.lower():
                                    mime_type = 'image/gif'
                                b64 = base64.b64encode(img_bytes).decode('utf-8')
                                parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64}"}
                                })
                                image_count += 1
                    if image_count > 0:
                        content_payload = parts

                messages_to_send = hist_raw + [{"role": "user", "content": content_payload}]

                # Usar modelo con visión si hay imágenes
                model_to_use = MODEL_VISION if image_count > 0 else None

                resp_text = await chat_completion(
                    system_prompt=sys_prompt,
                    messages=messages_to_send,
                    model=model_to_use
                )

                # Guardar historial
                if cid not in self.conversation_cache:
                    self.conversation_cache[cid] = []
                self.conversation_cache[cid].append({"role": "user", "content": user_text})

                if resp_text and resp_text.strip():
                    self.conversation_cache[cid].append({"role": "assistant", "content": resp_text})
                    if len(self.conversation_cache[cid]) > 20:
                        self.conversation_cache[cid] = self.conversation_cache[cid][-20:]

                    if len(resp_text) > 2000:
                        sent = await ctx.reply(resp_text[:2000])
                    else:
                        sent = await ctx.reply(resp_text)
                    return sent  # retornar el mensaje para follow-up en main.py
                else:
                    return await ctx.reply("no pude generar respuesta, intentalo de nuevo 😅")

            except Exception as e:
                print(f"Error Ask: {e}")
                import traceback
                traceback.print_exc()
                # Notificar sin re-raise para no duplicar mensajes de error
                try:
                    owner = await self.bot.fetch_user(self.bot.owner_id)
                    await owner.send(f"Error silencioso en ask:\n```{str(e)[:500]}```")
                except Exception:
                    pass
                return await ctx.reply("algo salió mal por acá 😵 ya lo revisaré")


async def setup(bot):
    await bot.add_cog(AskCog(bot))
    print("Commands.ask cargado.")