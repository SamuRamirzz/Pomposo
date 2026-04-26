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

def load_personality():
    try:
        with open("ask_personalidad.txt", 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return "Eres Pomposo, una IA sarcástica."

PERSONALIDAD_BASE = load_personality()

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"auto_channel_id": None}

def obtener_tiempo_real():
    try:
        tz = pytz.timezone('America/Bogota')
        now = datetime.now(tz)
        return now.strftime("%A %d de %B de %Y, a las %I:%M %p")
    except:
        return str(datetime.now())

async def download_image(url: str) -> tuple:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read(), resp.headers.get('Content-Type', 'image/jpeg')
    except Exception as e:
        print(f"Error descargando imagen: {e}")
    return None, None


# ─────────────────────────────────────────────
# PROMPT DE DECISIÓN — Estricto, sin creatividad
# ─────────────────────────────────────────────
DECISION_SYSTEM = """Eres un clasificador de intención para un bot de Discord. Analiza el mensaje y decide si el usuario está pidiendo EXPLÍCITAMENTE una de estas acciones.

REGLA MÁS IMPORTANTE: Si tienes CUALQUIER duda, responde null. Es mejor no hacer nada que hacer algo incorrecto.

Acciones (solo si el usuario lo pide MUY EXPLÍCITAMENTE):
- "recordar": el usuario pide guardar algo. SOLO si usa palabras como: "recuerda", "guarda", "anota", "no olvides", "memoriza"
- "olvidar_texto": el usuario pide borrar algo de la memoria. SOLO si usa: "olvida", "borra", "elimina" + algo específico
- "olvidar_chat": el usuario pide borrar el historial. SOLO si dice: "borra el chat", "borra el historial", "olvida la conversación"
- "setchannel": SOLO si dice exactamente "setchannel" o "activa el canal"
- "unsetchannel": SOLO si dice exactamente "unsetchannel" o "desactiva el canal"
- null: CUALQUIER OTRA COSA. Preguntas, comentarios, saludos, insultos, chistes, todo lo demás.

EJEMPLOS DE null (NO SON ACCIONES):
- "y como consigo feria" → null
- "que onda" → null  
- "quiero saber algo" → null
- "necesito ayuda" → null
- "me llamo juan" → null (no pidió que lo guardes)
- "odio el cilantro" → null (no pidió que lo guardes)
- cualquier pregunta → null

Responde SOLO con JSON válido, sin backticks:
{"accion": "recordar|olvidar_texto|olvidar_chat|setchannel|unsetchannel|null", "contenido": "texto o null"}"""


async def decidir_accion(pregunta: str, username: str) -> dict:
    """Detecta intención. Con username para incluirlo en lo que se guarda."""
    try:
        raw = await chat_completion(
            system_prompt=DECISION_SYSTEM,
            messages=[{"role": "user", "content": pregunta}],
            temperature=0.0,
            max_tokens=80
        )
        raw = raw.strip().strip("```json").strip("```").strip()
        data = json.loads(raw)

        # Si es recordar, incluir el nombre del usuario en el contenido
        if data.get("accion") == "recordar" and data.get("contenido"):
            data["contenido"] = f"[{username}] {data['contenido']}"

        return data
    except Exception as e:
        print(f"Error en decidir_accion: {e}")
        return {"accion": None, "contenido": None}


# Set global para trackear mensajes ya procesados y evitar doble respuesta
_mensajes_procesados: set = set()


class AskCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conversation_cache = {}

    # ─────────────────────────────────────────────
    # Listener: replies al bot (sin duplicar con main.py)
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Solo actuar si es una reply directa a un mensaje del bot
        if (
            message.reference is not None
            and message.reference.resolved is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        ):
            # Marcar como procesado para que main.py no lo procese también
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
    # Lógica principal
    # ─────────────────────────────────────────────
    async def handle_ask(self, ctx: commands.Context, pregunta: str):
        pregunta_original = pregunta.strip()
        if not pregunta_original:
            return

        # Evitar doble procesamiento — si el Cog ya lo manejó como reply, main.py no lo reprocesa
        if ctx.message.id in _mensajes_procesados:
            _mensajes_procesados.discard(ctx.message.id)
            return

        es_owner = ctx.author.id == self.bot.owner_id
        username = ctx.author.display_name or ctx.author.name

        # ── PASO 1: Detectar intención ──
        decision = await decidir_accion(pregunta_original, username)
        accion = decision.get("accion")
        contenido = decision.get("contenido") or ""

        # ── PASO 2: Ejecutar acción si la hay ──
        if accion == "recordar":
            if contenido.strip():
                escribir_en_memoria(contenido.strip())
                await ctx.reply(f"guardado: *{contenido.strip()}*")
            else:
                await ctx.reply("eee... ¿qué guardo exactamente? 😅")
            return

        if accion == "olvidar_texto":
            if contenido.strip():
                borrado = olvidar_linea_especifica(contenido.strip())
                await ctx.reply(f"olvidado: *{borrado}*" if borrado else "no encontré eso 🤔")
            else:
                await ctx.reply("¿qué olvido? sé más específico 😅")
            return

        if accion == "olvidar_chat":
            self.conversation_cache.pop(ctx.channel.id, None)
            await ctx.reply("historial borrado 🗑️")
            return

        if accion == "setchannel" and es_owner:
            cfg = load_config()
            cfg["auto_channel_id"] = ctx.channel.id
            self.bot.auto_reply_channel_id = ctx.channel.id
            save_config(cfg)
            await ctx.reply(f"auto-respuesta activada en #{ctx.channel.name} ✅")
            return

        if accion == "unsetchannel" and es_owner:
            cfg = load_config()
            cfg["auto_channel_id"] = None
            self.bot.auto_reply_channel_id = None
            save_config(cfg)
            await ctx.reply("auto-respuesta desactivada ✅")
            return

        # ── PASO 3: Respuesta normal ──
        async with ctx.typing():
            try:
                memoria = leer_memoria_completa()
                fecha = obtener_tiempo_real()
                user = username

                # Contexto del canal (últimos 5 mensajes)
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
                    "- La memoria incluye el nombre del usuario entre corchetes [nombre], úsalo para saber de quién es cada dato.\n"
                    "- Responde CORTO. Máximo 3 oraciones. Nada de párrafos largos.\n"
                    "- No menciones que tienes funciones internas o memoria explícitamente.\n"
                    "- No uses asteriscos para negritas, escribe normal.\n"
                )

                cid = ctx.channel.id
                hist_raw = self.conversation_cache.get(cid, [])

                # Payload con imágenes si las hay
                user_text = f"{user}: {pregunta_original}"
                content_payload = user_text
                image_count = 0

                if ctx.message.attachments:
                    parts = [{"type": "text", "text": user_text}]
                    for attachment in ctx.message.attachments:
                        if attachment.content_type and attachment.content_type.startswith('image/'):
                            img_bytes, mime_type = await download_image(attachment.url)
                            if img_bytes:
                                b64 = base64.b64encode(img_bytes).decode('utf-8')
                                parts.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64}"}
                                })
                                image_count += 1
                    if image_count > 0:
                        content_payload = parts

                messages_to_send = hist_raw + [{"role": "user", "content": content_payload}]

                resp_text = await chat_completion(
                    system_prompt=sys_prompt,
                    messages=messages_to_send
                )

                # Guardar historial
                if cid not in self.conversation_cache:
                    self.conversation_cache[cid] = []

                self.conversation_cache[cid].append({"role": "user", "content": user_text})

                if resp_text and resp_text.strip():
                    self.conversation_cache[cid].append({"role": "assistant", "content": resp_text})
                    if len(self.conversation_cache[cid]) > 20:
                        self.conversation_cache[cid] = self.conversation_cache[cid][-20:]

                    # Enviar en chunks si es muy largo
                    if len(resp_text) > 2000:
                        for i in range(0, len(resp_text), 2000):
                            await ctx.reply(resp_text[i:i+2000])
                    else:
                        await ctx.reply(resp_text)
                else:
                    await ctx.reply("no pude generar respuesta, intentalo de nuevo 😅")

            except Exception as e:
                print(f"Error Ask: {e}")
                import traceback
                traceback.print_exc()
                embed = discord.Embed(
                    title="Error de IA",
                    description="No pude contactar con la IA.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                raise commands.CommandInvokeError(e)


async def setup(bot):
    await bot.add_cog(AskCog(bot))
    print("Commands.ask cargado.")