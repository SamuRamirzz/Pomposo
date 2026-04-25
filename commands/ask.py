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

print(" Configuración inicializada (Usando Gemini API)")

# --- Constantes ---
CONFIG_FILE = "bot_config.json"

# --- Memoria ---
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mongo_memory import (
    leer_memoria_completa,
    leer_memoria_lineas,
    escribir_en_memoria,
    reescribir_memoria_lineas,
    olvidar_por_texto as olvidar_linea_especifica
)

# --- Personalidad ---
def load_personality():
    try:
        with open("ask_personalidad.txt", 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return "Eres Pomposo, una IA sarcástica."

PERSONALIDAD_BASE = load_personality()

# --- Config ---
def save_config(data):
    with open(CONFIG_FILE, 'w') as f: json.dump(data, f, indent=4)

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f: return json.load(f)
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
                    image_bytes = await resp.read()
                    content_type = resp.headers.get('Content-Type', 'image/jpeg')
                    return image_bytes, content_type
    except Exception as e:
        print(f"Error descargando imagen: {e}")
    return None, None


# ─────────────────────────────────────────────
# PROMPT DE DECISIÓN — La IA decide qué hacer
# ─────────────────────────────────────────────
DECISION_SYSTEM = """
Eres el módulo de decisión de un bot de Discord llamado Pomposo.
Tu única tarea es analizar el mensaje del usuario y decidir si quiere que el bot ejecute una acción interna.

Las acciones disponibles son:
- "recordar": el usuario quiere que el bot guarde algo en su memoria persistente.
  Ejemplos: "guarda que me llamo Juan", "recuerda que odio el cilantro", "anota esto: ...", "no te olvides de que..."
- "olvidar_texto": el usuario quiere que el bot borre algo específico de su memoria.
  Ejemplos: "olvida lo de Juan", "borra que odio el cilantro", "elimina eso que dijiste de..."
- "olvidar_chat": el usuario quiere borrar el historial de conversación del canal (no la memoria persistente).
  Ejemplos: "borra el chat", "olvida nuestra conversación", "limpia el historial"
- "setchannel": el usuario (owner) quiere activar auto-respuesta en el canal actual.
  Ejemplos: "activa el canal", "pon el setchannel aquí", "auto-responde en este canal"
- "unsetchannel": el usuario (owner) quiere desactivar la auto-respuesta.
  Ejemplos: "desactiva el canal", "quita el setchannel", "para de auto-responder"
- null: el usuario solo quiere hablar, preguntar algo, o no hay ninguna acción clara.

Responde ÚNICAMENTE con un objeto JSON válido, sin texto extra, sin backticks, sin explicaciones.
El formato es:
{
  "accion": "<recordar|olvidar_texto|olvidar_chat|setchannel|unsetchannel|null>",
  "contenido": "<el texto exacto a recordar u olvidar, o null si no aplica>"
}

Ejemplos:
- Mensaje: "recuerda que mi color favorito es el azul"
  Respuesta: {"accion": "recordar", "contenido": "el color favorito del usuario es el azul"}

- Mensaje: "olvida lo del color favorito"
  Respuesta: {"accion": "olvidar_texto", "contenido": "color favorito"}

- Mensaje: "borra la conversación"
  Respuesta: {"accion": "olvidar_chat", "contenido": null}

- Mensaje: "qué hora es?"
  Respuesta: {"accion": null, "contenido": null}

- Mensaje: "cuéntame un chiste"
  Respuesta: {"accion": null, "contenido": null}
"""


async def decidir_accion(pregunta: str) -> dict:
    """
    Llama a la IA con el prompt de decisión para que analice la intención del usuario.
    Retorna {"accion": ..., "contenido": ...}
    """
    try:
        raw = await chat_completion(
            system_prompt=DECISION_SYSTEM,
            messages=[{"role": "user", "content": pregunta}],
            temperature=0.0,   # 0 para máxima consistencia en clasificación
            max_tokens=100
        )
        # Limpiar backticks por si acaso
        raw = raw.strip().strip("```json").strip("```").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"Error en decidir_accion: {e}")
        return {"accion": None, "contenido": None}


class AskCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conversation_cache = {}
        self.CACHE_LIMIT = 10

    # ─────────────────────────────────────────────
    # Listener: responder cuando alguien responde al bot
    # ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignorar mensajes del propio bot
        if message.author.bot:
            return

        # Verificar si es una reply a un mensaje del bot
        if (
            message.reference is not None
            and message.reference.resolved is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        ):
            # Crear un contexto falso para reusar handle_ask
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
    # Lógica principal (reutilizable por ask y on_message)
    # ─────────────────────────────────────────────
    async def handle_ask(self, ctx: commands.Context, pregunta: str):
        pregunta_original = pregunta.strip()
        if not pregunta_original:
            return

        es_owner = ctx.author.id == self.bot.owner_id

        # ── PASO 1: La IA decide si hay una acción interna ──
        decision = await decidir_accion(pregunta_original)
        accion   = decision.get("accion")
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
                await ctx.reply(f"olvidado: *{borrado}*" if borrado else "no encontré eso en mi memoria 🤔")
            else:
                await ctx.reply("¿qué olvido? sé más específico 😅")
            return

        if accion == "olvidar_chat":
            self.conversation_cache.pop(ctx.channel.id, None)
            await ctx.reply("historial de conversación borrado 🗑️")
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

        # ── PASO 3: Respuesta normal de la IA ──
        async with ctx.typing():
            try:
                memoria  = leer_memoria_completa()
                fecha    = obtener_tiempo_real()
                user     = ctx.author.name.lower()

                # Contexto del canal (últimos 5 mensajes)
                canal_context = ""
                try:
                    mensajes_recientes = []
                    async for msg in ctx.channel.history(limit=6):
                        if msg.id == ctx.message.id:
                            continue
                        prefix = "[BOT]" if msg.author.bot else ""
                        mensajes_recientes.append(f"{prefix} {msg.author.name}: {msg.content[:150]}")
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
                    'hoy', 'ayer', 'mañana', 'fecha', 'hora',
                    'cuanto', 'cuánto', 'cuántos', 'vale', 'cuesta',
                    'qué es', 'que es', 'define', 'significado',
                ]
                needs_search = any(kw in pregunta_original.lower() for kw in SEARCH_KEYWORDS)
                search_context = ""
                if needs_search:
                    def _sync_ddg(q):
                        from duckduckgo_search import DDGS
                        with DDGS() as ddgs:
                            return list(ddgs.text(q, region='wt-wt', safesearch='moderate', max_results=3))
                    try:
                        results = await asyncio.to_thread(_sync_ddg, pregunta_original)
                        if results:
                            search_context = "[RESULTADOS WEB EN TIEMPO REAL]\n"
                            for r in results:
                                search_context += f"- {r.get('title')}: {r.get('body')}\n"
                    except Exception as e:
                        print(f"Error DuckDuckGo: {e}")

                sys_prompt = f"""
{PERSONALIDAD_BASE}
[TIEMPO REAL] {fecha}
[USUARIO] {user}
[MEMORIA PERSISTENTE - MUY IMPORTANTE]
La siguiente es tu memoria a largo plazo. Son hechos que DEBES recordar y aplicar en TODAS tus respuestas sin excepción. Nunca digas que no recuerdas algo que esté aquí:
{memoria}
[CHAT RECIENTE DEL CANAL]
{canal_context if canal_context else "(sin mensajes recientes)"}
{search_context}
[INSTRUCCIONES]
- La MEMORIA PERSISTENTE contiene hechos absolutos sobre ti y las personas del servidor. Úsala siempre.
- Nunca digas "no tengo memoria" o "no recuerdo" si el dato está en tu MEMORIA PERSISTENTE.
- Los RESULTADOS WEB (si los hay) son para informarte de la actualidad antes de responder.
- El chat reciente del canal es solo para contexto. NO lo menciones a menos que sea directamente relevante.
- Si alguien habla de otra persona del chat, usa el contexto para entender de quién hablan.
- NO menciones que tienes un sistema de acciones o funciones internas. Actúa natural.
"""

                cid = ctx.channel.id
                hist_raw = self.conversation_cache.get(cid, [])

                # Construir payload con imágenes si las hay
                user_text = f"{user}: {pregunta_original}"
                content_payload = []
                image_count = 0

                if ctx.message.attachments:
                    content_payload.append({"type": "text", "text": user_text})
                    for attachment in ctx.message.attachments:
                        if attachment.content_type and attachment.content_type.startswith('image/'):
                            img_bytes, mime_type = await download_image(attachment.url)
                            if img_bytes:
                                b64 = base64.b64encode(img_bytes).decode('utf-8')
                                content_payload.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64}"}
                                })
                                image_count += 1

                if image_count == 0:
                    content_payload = user_text

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

                    if len(resp_text) > 2000:
                        await ctx.reply(resp_text[:2000])
                    else:
                        await ctx.reply(resp_text)
                else:
                    await ctx.reply("la ia no pudo generar una respuesta, intentalo de nuevo 😅")

            except Exception as e:
                print(f"Error Ask: {e}")
                import traceback
                traceback.print_exc()
                error_embed = discord.Embed(
                    title="Error de IA",
                    description="No pude contactar con la IA.",
                    color=discord.Color.red()
                )
                error_embed.set_footer(text="El sistema de auto-reparación ha sido notificado.")
                await ctx.send(embed=error_embed)
                raise commands.CommandInvokeError(e)


async def setup(bot):
    await bot.add_cog(AskCog(bot))
    print(" Commands.ask cargado.")