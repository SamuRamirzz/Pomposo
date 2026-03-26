import os
import json
import discord
from discord import app_commands
from google import genai
from google.genai import types
from discord.ext import commands
from fuzzywuzzy import fuzz
from datetime import datetime
import pytz
import aiohttp

# --- Configuración de Gemini (API Gratuita + Google Search) ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        print(" Conectado a Gemini (con Google Search y Conciencia Temporal).")
    except Exception as e:
        print(f" Error al inicializar Gemini: {e}")
else:
    print("  Advertencia: Falta 'GEMINI_API_KEY' en .env.")

# --- Constantes ---
CONFIG_FILE = "bot_config.json"

# --- Memoria (MongoDB con fallback a archivo local) ---
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mongo_memory import (
    leer_memoria_completa,
    leer_memoria_lineas,
    escribir_en_memoria,
    reescribir_memoria_lineas,
    olvidar_por_texto as olvidar_linea_especifica,
    importar_desde_archivo
)

# Importar memoria existente a MongoDB al iniciar
importar_desde_archivo()

# --- Personalidad ---
def load_personality():
    """Carga la personalidad desde ask_personalidad.txt"""
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
    """
    Descarga una imagen desde una URL y retorna (bytes, mime_type).
    Retorna (None, None) si falla.
    """
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


class AskCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conversation_cache = {}
        self.CACHE_LIMIT = 10

    @app_commands.command(name="ask", description="Habla con el pequeño pomposo")
    @app_commands.describe(pregunta="Lo que quieres decirele a pomposo")
    async def ask_slash(self, interaction: discord.Interaction, pregunta: str):
        """Slash command para ask."""
        await self.ask(await self.bot.get_context(interaction), pregunta=pregunta)

    @commands.command(name="ask", description="Habla con la IA.")
    async def ask(self, ctx: commands.Context, *, pregunta: str = ""):
        if not client:
            await ctx.send(" Error: API de Gemini no configurada.", ephemeral=True)
            return
        
        pregunta_original = pregunta.strip()
        pregunta_lower = pregunta_original.lower()

        # Si no hay pregunta pero hay imágenes, usar texto por defecto
        if not pregunta and ctx.message.attachments:
            pregunta = "¿Qué ves en esta imagen?"
            pregunta_original = pregunta.strip()
            pregunta_lower = pregunta_original.lower()
        palabra_clave = pregunta_lower.split(' ')[0] if pregunta_lower else ""

        score_recuerda = fuzz.ratio(palabra_clave, "recuerda")
        score_guarda = fuzz.partial_ratio("guarda en tu memoria", pregunta_lower[:25])
        score_olvida = fuzz.ratio(palabra_clave, "olvida")
        score_setchannel = fuzz.ratio(palabra_clave, "setchannel")
        score_unsetchannel = fuzz.ratio(palabra_clave, "unsetchannel")

        UMBRAL_CONFIANZA = 80
        es_owner = ctx.author.id == self.bot.owner_id

        # --- Comandos Simples ---
        if score_recuerda > UMBRAL_CONFIANZA or score_guarda > UMBRAL_CONFIANZA:
            if score_guarda > UMBRAL_CONFIANZA:
                partes = pregunta_original.split(' ', 4)
                texto = partes[4] if len(partes) > 4 else ""
            else:
                partes = pregunta_original.split(' ', 1)
                texto = partes[1] if len(partes) > 1 else ""
            if texto.strip():
                escribir_en_memoria(texto.strip())
                await ctx.reply(f"guardado: *{texto.strip()}*")
            else:
                await ctx.send("¿qué guardo?", ephemeral=True)
            return

        if score_olvida > UMBRAL_CONFIANZA:
            partes = pregunta_original.split(' ', 1)
            texto = partes[1].strip() if len(partes) > 1 else ""
            if "chat" in texto or "conversacion" in texto:
                self.conversation_cache.pop(ctx.channel.id, None)
                await ctx.reply("historial borrado.")
                return
            if texto:
                borrado = olvidar_linea_especifica(texto)
                await ctx.reply(f"olvidado: {borrado}" if borrado else "no encontré eso")
            else:
                await ctx.send("¿qué olvido?", ephemeral=True)
            return

        if es_owner and "channel" in palabra_clave:
            cfg = load_config()
            if "un" in palabra_clave:
                cfg["auto_channel_id"] = None
                self.bot.auto_reply_channel_id = None
                await ctx.reply(" Auto-respuesta off.")
            else:
                cfg["auto_channel_id"] = ctx.channel.id
                self.bot.auto_reply_channel_id = ctx.channel.id
                await ctx.reply(f" Auto-respuesta en #{ctx.channel.name}")
            save_config(cfg)
            return

        # --- INTELIGENCIA 24/7 CON SOPORTE DE IMÁGENES ---
        async with ctx.typing():
            try:
                # 1. Contexto básico
                memoria = leer_memoria_completa()
                fecha = obtener_tiempo_real()
                user = ctx.author.name.lower()

                # 2. Contexto del canal (últimos 5 mensajes)
                canal_context = ""
                try:
                    mensajes_recientes = []
                    async for msg in ctx.channel.history(limit=6):  # 6 para excluir el comando actual
                        if msg.id == ctx.message.id:
                            continue
                        if msg.author.bot:
                            mensajes_recientes.append(f"[BOT] {msg.author.name}: {msg.content[:150]}")
                        else:
                            mensajes_recientes.append(f"{msg.author.name}: {msg.content[:150]}")
                    if mensajes_recientes:
                        mensajes_recientes.reverse()  # Orden cronológico
                        canal_context = "\n".join(mensajes_recientes[:5])
                except Exception:
                    canal_context = ""

                sys_prompt = f"""
                {PERSONALIDAD_BASE}
                [TIEMPO REAL] {fecha}
                [USUARIO] {user}
                [MEMORIA] {memoria}
                [CHAT RECIENTE DEL CANAL]
                {canal_context if canal_context else "(sin mensajes recientes)"}
                [INSTRUCCIONES]
                - Usa Google Search para noticias, fechas o datos exactos.
                - El chat reciente del canal es solo para que tengas contexto de la conversación. NO lo menciones a menos que sea directamente relevante a lo que te preguntan.
                - Si alguien habla de otra persona del chat, puedes usar el contexto para entender de quién hablan.
                """

                # 2. Historial
                cid = ctx.channel.id
                hist_raw = self.conversation_cache.get(cid, [])

                gemini_hist = []
                for h in hist_raw:
                    gemini_hist.append(types.Content(role=h.role, parts=h.parts))

                # 3. Mensaje Usuario + Imágenes
                parts = []
                user_text = f"{user}: {pregunta_original}"
                parts.append(types.Part(text=user_text))

                # Procesar imágenes adjuntas
                image_count = 0
                if ctx.message.attachments:
                    for attachment in ctx.message.attachments:
                        if attachment.content_type and attachment.content_type.startswith('image/'):
                            img_bytes, mime_type = await download_image(attachment.url)
                            if img_bytes:
                                parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
                                image_count += 1
                                print(f" Imagen adjuntada: {attachment.filename} ({mime_type})")

                # 4. Herramientas (Google Search solo cuando se necesita)
                SEARCH_KEYWORDS = [
                    'quien', 'quién', 'cuando', 'cuándo', 'donde', 'dónde',
                    'noticias', 'precio', 'clima', 'temperatura', 'ganó',
                    'resultado', 'estreno', 'lanzamiento', 'actualidad',
                    'hoy', 'ayer', 'mañana', 'fecha', 'hora',
                    'cuanto', 'cuánto', 'cuántos', 'vale', 'cuesta',
                    'qué es', 'que es', 'define', 'significado',
                ]
                pregunta_lower_check = pregunta_original.lower()
                needs_search = any(kw in pregunta_lower_check for kw in SEARCH_KEYWORDS)
                
                tools = [types.Tool(google_search=types.GoogleSearch())] if needs_search else []

                # 5. Ejecutar IA
                config_kwargs = {
                    'system_instruction': sys_prompt,
                    'temperature': 0.8,
                }
                if tools:
                    config_kwargs['tools'] = tools

                chat = client.chats.create(
                    model='gemini-2.5-flash',
                    config=types.GenerateContentConfig(**config_kwargs),
                    history=gemini_hist
                )

                # Enviar mensaje con todas las partes (texto + imágenes)
                resp = chat.send_message(parts)

                # 6. Guardar Historial (solo texto, no imágenes para ahorrar memoria)
                if cid not in self.conversation_cache: 
                    self.conversation_cache[cid] = []

                # Guardar solo la parte de texto en el historial
                text_part = types.Part(text=user_text)
                self.conversation_cache[cid].append(types.Content(role='user', parts=[text_part]))

                if resp.text and resp.text.strip():
                    model_part = types.Part(text=resp.text)
                    self.conversation_cache[cid].append(types.Content(role='model', parts=[model_part]))

                    if len(self.conversation_cache[cid]) > 20:
                        self.conversation_cache[cid] = self.conversation_cache[cid][-20:]

                    if len(resp.text) > 2000:
                        await ctx.reply(resp.text[:2000])
                    else:
                        await ctx.reply(resp.text)
                else:
                    # No enviar mensaje vacío, enviar un mensaje de error
                    await ctx.reply("La IA no pudo generar una respuesta. Intenta de nuevo.")

            except Exception as e:
                print(f"Error Ask: {e}")
                import traceback
                traceback.print_exc()
                # Enviar mensaje limpio al usuario
                error_embed = discord.Embed(
                    title=" Error de IA",
                    description=f"No pude contactar con la IA.",
                    color=discord.Color.red()
                )
                error_embed.set_footer(text="El sistema de auto-reparación ha sido notificado.")
                await ctx.send(embed=error_embed)
                # Re-lanzar para que on_command_error active el auto-diagnóstico
                raise commands.CommandInvokeError(e)


async def setup(bot):
    await bot.add_cog(AskCog(bot))
    print(" Commands.ask cargado.")