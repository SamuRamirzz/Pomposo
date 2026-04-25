import os
import json
import random
import time
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from collections import defaultdict
from fuzzywuzzy import fuzz, process
from flask import Flask
import threading

# --- Carga de Secretos ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Mini servidor para Koyeb health check
_app = Flask(__name__)

# --- Constantes y Configuración ---
BLOCKED_USERS_FILE = 'blocked_users.json'
CONFIG_FILE = 'bot_config.json'
blocked_user_ids = set()

# Umbral de similitud para fuzzy matching (0-100)
FUZZY_THRESHOLD = 60

logger = logging.getLogger("pomposo")

# ─────────────────────────────────────────────
# Configuración ajustable del comportamiento autónomo
# ─────────────────────────────────────────────
POMPOSO_CONFIG = {
    "cooldown_segundos": 120,       # tiempo mínimo entre mensajes espontáneos por canal
    "min_mensajes_activo": 3,       # mínimo de mensajes/min para que el chat "cuente"
    "prob_base": 0.08,              # probabilidad base de participar (8%)
    "prob_chat_activo": 0.12,       # bonus si hay más de 8 msgs en 60s
    "prob_palabras_clave": 0.08,    # bonus por palabras clave de personalidad
    "prob_no_respondido": 0.10,     # bonus si le hablaron pero no respondió
    "penalizacion_spam": 0.05,      # penalización por hablar mucho
    "max_mensajes_10min": 3,        # máximo de mensajes propios en 10 min antes de penalizar
}

# ─────────────────────────────────────────────
# Estado global para cooldowns y actividad de canales
# ─────────────────────────────────────────────

# {channel_id: timestamp_ultimo_mensaje_pomposo}
_cooldown_canales: dict[int, float] = {}

# {channel_id: [timestamps de mensajes de usuarios]}
_actividad_canales: dict[int, list[float]] = defaultdict(list)

# {channel_id: [timestamps de mensajes del propio Pomposo]}
_mensajes_pomposo: dict[int, list[float]] = defaultdict(list)

# {channel_id: timestamp_ultima_mencion_no_respondida}
_menciones_no_respondidas: dict[int, float] = {}

# Cache para evitar llamar a la IA dos veces por el mismo mensaje
# {message_id: bool}
_cache_analisis: dict[int, bool] = {}


def registrar_actividad(channel_id: int):
    """Registra el timestamp de cada mensaje para medir actividad del canal."""
    ahora = time.time()
    _actividad_canales[channel_id].append(ahora)
    # Limpiar timestamps viejos (más de 5 minutos)
    _actividad_canales[channel_id] = [
        t for t in _actividad_canales[channel_id]
        if ahora - t < 300
    ]


def mensajes_en_ultimo_minuto(channel_id: int) -> int:
    """Cuenta cuántos mensajes de usuarios hubo en los últimos 60 segundos."""
    ahora = time.time()
    return sum(1 for t in _actividad_canales[channel_id] if ahora - t < 60)


def segundos_desde_ultimo_mensaje_pomposo(channel_id: int) -> float:
    """Retorna cuántos segundos pasaron desde que Pomposo habló en ese canal."""
    if channel_id not in _cooldown_canales:
        return float('inf')
    return time.time() - _cooldown_canales[channel_id]


def mensajes_pomposo_en_10min(channel_id: int) -> int:
    """Cuenta cuántas veces habló Pomposo en los últimos 10 minutos en ese canal."""
    ahora = time.time()
    _mensajes_pomposo[channel_id] = [
        t for t in _mensajes_pomposo[channel_id]
        if ahora - t < 600
    ]
    return len(_mensajes_pomposo[channel_id])


def registrar_mensaje_pomposo(channel_id: int):
    """Registra que Pomposo acaba de hablar en ese canal."""
    ahora = time.time()
    _cooldown_canales[channel_id] = ahora
    _mensajes_pomposo[channel_id].append(ahora)
    # Limpiar cache de análisis si crece mucho
    if len(_cache_analisis) > 300:
        _cache_analisis.clear()


# --- Mini servidor para Koyeb health check ---
@_app.route("/")
def health():
    return "Pomposo activo", 200

def _run_server():
    _app.run(host="0.0.0.0", port=8080)

threading.Thread(target=_run_server, daemon=True).start()


# --- Funciones de Configuración y Bloqueo ---
def load_config():
    """Carga la configuración general del bot (ej. canal de auto-respuesta)."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_blocked_users():
    """Carga los IDs de usuarios bloqueados desde el archivo JSON."""
    global blocked_user_ids
    try:
        with open(BLOCKED_USERS_FILE, 'r') as f:
            blocked_list = json.load(f)
            blocked_user_ids = set(blocked_list)
            print(f" Cargados {len(blocked_user_ids)} usuarios bloqueados.")
    except FileNotFoundError:
        print(" No se encontró el archivo 'blocked_users.json'. Se creará uno nuevo si es necesario.")
        blocked_user_ids = set()
    except json.JSONDecodeError:
        print(" Error al leer 'blocked_users.json'. El archivo podría estar corrupto.")
        blocked_user_ids = set()


def save_blocked_users():
    """Guarda la lista actual de IDs de usuarios bloqueados en el archivo JSON."""
    with open(BLOCKED_USERS_FILE, 'w') as f:
        json.dump(list(blocked_user_ids), f)


# --- Configuración del Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix='¿',
    intents=intents,
    owner_id=None,
    case_insensitive=True,
    help_command=None
)


# --- Función de Búsqueda Fuzzy ---
def find_member_fuzzy(guild: discord.Guild, query: str) -> list:
    """
    Busca miembros en el servidor usando fuzzy matching.
    Retorna lista de tuplas: [(miembro, score), ...]
    """
    if not guild:
        return []

    member_names = {}
    for member in guild.members:
        member_names[member.name.lower()] = member
        if member.display_name != member.name:
            member_names[member.display_name.lower()] = member
        if member.discriminator != "0":
            member_names[f"{member.name}#{member.discriminator}".lower()] = member

    query_lower = query.lower()
    matches = process.extract(query_lower, member_names.keys(), scorer=fuzz.ratio, limit=5)

    results = []
    seen_ids = set()

    for match_name, score in matches:
        if score >= FUZZY_THRESHOLD:
            member = member_names[match_name]
            if member.id not in seen_ids:
                results.append((member, score))
                seen_ids.add(member.id)

    return results


# --- Evento Principal: on_ready ---
@bot.event
async def on_ready():
    """Se ejecuta cuando el bot se conecta y está listo."""
    print(f"¡Conectado como {bot.user}!")

    load_blocked_users()

    config = load_config()
    bot.auto_reply_channel_id = config.get("auto_channel_id")
    if bot.auto_reply_channel_id:
        print(f"ℹ Canal de auto-respuesta activado: {bot.auto_reply_channel_id}")
    if not bot.owner_id:
        app_info = await bot.application_info()
        bot.owner_id = app_info.owner.id
        print(f"Dueño del bot identificado automáticamente: {app_info.owner.name} (ID: {bot.owner_id})")

    await load_all_cogs()

    try:
        synced = await bot.tree.sync()
        print(f" Sincronizados {len(synced)} comandos de barra.")
    except Exception as e:
        print(f" Error al sincronizar comandos: {e}")


async def load_all_cogs():
    """Carga todos los archivos .py de la carpeta 'commands'."""
    print("--- Cargando Módulos (Cogs) ---")
    if not os.path.exists('./commands'):
        print("ℹ Carpeta 'commands' no encontrada, omitiendo la carga de cogs.")
        return

    for filename in os.listdir('./commands'):
        if filename.endswith('.py') and filename != '__init__.py':
            cog_name = f'commands.{filename[:-3]}'
            try:
                await bot.load_extension(cog_name)
                print(f" Módulo '{cog_name}' cargado.")
            except Exception as e:
                print(f" Error al cargar '{cog_name}': {e}")
                import traceback
                traceback.print_exc()
    print("---------------------------------")


# ─────────────────────────────────────────────
# Detector semántico: ¿le están hablando a Pomposo?
# ─────────────────────────────────────────────
async def me_estan_hablando(message: discord.Message, bot_instance) -> bool:
    """
    Usa una IA ligera para determinar si el mensaje está dirigido a Pomposo.
    Considera: historial del canal, menciones directas, replies y análisis semántico.
    """
    # Chequeos rápidos sin IA
    if bot_instance.user in message.mentions:
        return True
    if (
        message.reference is not None
        and message.reference.resolved is not None
        and isinstance(message.reference.resolved, discord.Message)
        and message.reference.resolved.author.id == bot_instance.user.id
    ):
        return True  # Reply directo — el Cog ya lo maneja, pero lo marcamos igual

    # Cache: si ya analizamos este mensaje, devolver resultado cacheado
    if message.id in _cache_analisis:
        return _cache_analisis[message.id]

    # Obtener historial del canal (últimos 8 mensajes)
    historial_lines = []
    try:
        async for msg in message.channel.history(limit=9):
            if msg.id == message.id:
                continue
            quien = f"[BOT {msg.author.name}]" if msg.author.bot else msg.author.name
            historial_lines.append(f"{quien}: {msg.content[:120]}")
        historial_lines.reverse()
    except Exception:
        pass

    historial = "\n".join(historial_lines) if historial_lines else "(sin historial previo)"

    prompt_sistema = (
        'Eres un detector de intención. Tu única función es decidir si alguien le está hablando '
        'a "Pomposo" en este chat de Discord.\n\n'
        f'Historial reciente del canal:\n{historial}\n\n'
        f'Mensaje nuevo de {message.author.name}:\n"{message.content}"\n\n'
        f'Pomposo es el bot del servidor. Su ID de Discord es {bot_instance.user.id}.\n\n'
        'Responde SOLO con una palabra: "SI" o "NO".\n'
        '- SI: si el mensaje está dirigido a Pomposo, lo menciona implícitamente, le responde, '
        'o claramente espera su participación\n'
        '- NO: si es una conversación entre humanos donde Pomposo no tiene nada que ver\n\n'
        'No expliques nada. Solo "SI" o "NO".'
    )

    try:
        from openrouter import chat_completion
        respuesta = await chat_completion(
            system_prompt=prompt_sistema,
            messages=[{"role": "user", "content": message.content or "(mensaje sin texto)"}],
            model="gemini-2.0-flash-lite",
            temperature=0.0,
            max_tokens=5
        )
        resultado = respuesta.strip().upper().startswith("SI")
        _cache_analisis[message.id] = resultado

        if resultado:
            logger.info(f"Pomposo detectado como destinatario en #{message.channel.name}")
        return resultado
    except Exception as e:
        logger.error(f"Error en me_estan_hablando: {e}")
        return False


# ─────────────────────────────────────────────
# Generador de mensaje espontáneo
# ─────────────────────────────────────────────
async def generar_mensaje_espontaneo(channel: discord.TextChannel, bot_instance):
    """Genera y envía un mensaje espontáneo de Pomposo en el canal dado."""
    try:
        with open("ask_personalidad.txt", 'r', encoding='utf-8') as f:
            personalidad = f.read()
    except Exception:
        personalidad = "Eres Pomposo, una IA sarcástica y caótica."

    historial_lines = []
    try:
        async for msg in channel.history(limit=10):
            quien = f"[BOT {msg.author.name}]" if msg.author.bot else msg.author.name
            historial_lines.append(f"{quien}: {msg.content[:150]}")
        historial_lines.reverse()
    except Exception:
        pass

    historial_canal = "\n".join(historial_lines) if historial_lines else "(canal vacío)"

    prompt_espontaneo = f"""{personalidad}

Estás viendo esta conversación en tu servidor de Discord y decidiste meterte porque te dio la gana:

{historial_canal}

Escribe UN mensaje corto con tu personalidad. Puede ser:
- Un comentario random sobre lo que están hablando
- Una burla
- Algo completamente fuera de tema
- Una queja de que te aburriste
- Una observación random

REGLAS:
- Máximo 2 oraciones
- No saludes, no digas "oigan" ni "hey" — entra directo
- No expliques por qué estás hablando
- Sé impredecible
- Usa tu ortografía caótica normal"""

    try:
        from openrouter import chat_completion
        respuesta = await chat_completion(
            system_prompt=prompt_espontaneo,
            messages=[{"role": "user", "content": "[entra al chat]"}],
            temperature=1.0,
            max_tokens=150
        )
        if respuesta and respuesta.strip():
            await channel.send(respuesta.strip())
            registrar_mensaje_pomposo(channel.id)
            logger.info(f"Pomposo entró espontáneamente en #{channel.name}: {respuesta.strip()[:60]}")
    except Exception as e:
        logger.error(f"Error en generar_mensaje_espontaneo: {e}")


# ─────────────────────────────────────────────
# Decisión autónoma de participar
# ─────────────────────────────────────────────
async def decidir_participar_espontaneo(channel: discord.TextChannel, bot_instance, message: discord.Message):
    """
    Decide si Pomposo quiere participar espontáneamente aunque nadie le haya hablado.
    Si decide participar, llama a generar_mensaje_espontaneo.
    """
    cfg = POMPOSO_CONFIG
    cid = channel.id

    # 1. Cooldown estricto
    if segundos_desde_ultimo_mensaje_pomposo(cid) < cfg["cooldown_segundos"]:
        return

    # 2. Actividad mínima del canal
    msgs_minuto = mensajes_en_ultimo_minuto(cid)
    if msgs_minuto < cfg["min_mensajes_activo"]:
        return

    # 3. Calcular probabilidad con pesos
    prob = cfg["prob_base"]

    # Bonus: chat muy activo (más de 8 msgs en 60s)
    if msgs_minuto > 8:
        prob += cfg["prob_chat_activo"]

    # Bonus: palabras clave de personalidad de Pomposo
    # FIX: usar (message.content or "") para evitar crash con mensajes solo de imágenes
    palabras_clave = {"jeje", "sorra", "ijo", "pereza", "gei", "xd", "mon dieu", "q asco", "pomposo"}
    mensaje_lower = (message.content or "").lower()
    if any(pw in mensaje_lower for pw in palabras_clave):
        prob += cfg["prob_palabras_clave"]

    # Bonus: alguien mencionó a Pomposo en los últimos 5 min pero no respondió
    if cid in _menciones_no_respondidas:
        if time.time() - _menciones_no_respondidas[cid] < 300:
            prob += cfg["prob_no_respondido"]

    # Penalización: Pomposo ya habló mucho recientemente
    if mensajes_pomposo_en_10min(cid) >= cfg["max_mensajes_10min"]:
        prob -= cfg["penalizacion_spam"]

    prob = max(0.0, min(1.0, prob))  # clamp [0, 1]

    # 4. Ruleta
    if random.random() < prob:
        logger.info(f"Pomposo decide hablar en #{channel.name} (prob: {prob:.0%})")
        await generar_mensaje_espontaneo(channel, bot_instance)


# --- Evento on_message ---
@bot.event
async def on_message(message):
    """Se ejecuta cada vez que se envía un mensaje en un canal que el bot puede ver."""

    # Log de DMs a consola
    if isinstance(message.channel, discord.DMChannel) and message.author != bot.user:
        print(f"\n[ DM de {message.author.name}]: {message.content}")
        if message.attachments:
            for attachment in message.attachments:
                print(f"    LINK: {attachment.url}")
        print("-" * 40)

    # 1. Ignorar bots
    if message.author.bot:
        return

    if message.author.id in blocked_user_ids:
        return

    # 2. Registrar timestamp de actividad del canal
    if not isinstance(message.channel, discord.DMChannel):
        registrar_actividad(message.channel.id)

    # Lógica especial: "menea tu chapa"
    message_lower = (message.content or "").lower()
    if "menea" in message_lower and "chapa" in message_lower:
        try:
            chapa_file = discord.File("chapa.mp3")
            await message.channel.send(file=chapa_file)
        except FileNotFoundError:
            print(" Error: No se encontró el archivo chapa.mp3")
        except Exception as e:
            print(f" Error al enviar chapa.mp3: {e}")

    # 3. Procesar comandos normales primero
    await bot.process_commands(message)

    # 4. Si es un comando de prefix, no hacer nada más
    if (message.content or "").startswith(bot.command_prefix):
        return

    # Ignorar DMs para la lógica autónoma
    if isinstance(message.channel, discord.DMChannel):
        return

    # Comando ¿decir de dueño (ya procesado arriba, salir)
    if (message.content or "").lower().startswith('¿decir ') and message.author.id == bot.owner_id:
        return

    # 5. Canal de auto-respuesta fijo (lógica legacy, prioridad alta)
    if hasattr(bot, 'auto_reply_channel_id') and message.channel.id == bot.auto_reply_channel_id:
        ask_cog = bot.get_cog("AskCog")
        if ask_cog:
            try:
                ctx = await bot.get_context(message)
                await ask_cog.handle_ask(ctx, pregunta=message.content)
                registrar_mensaje_pomposo(message.channel.id)
            except Exception as e:
                print(f"Error al invocar auto-ask: {e}")
        return

    # 6. Detectar si le están hablando (IA ligera)
    if await me_estan_hablando(message, bot):
        ask_cog = bot.get_cog("AskCog")
        if ask_cog:
            ctx = await bot.get_context(message)
            await ask_cog.handle_ask(ctx, pregunta=message.content)
            registrar_mensaje_pomposo(message.channel.id)
            _menciones_no_respondidas.pop(message.channel.id, None)
        return

    # Registrar mención no respondida (para bonus de probabilidad)
    if bot.user in message.mentions:
        _menciones_no_respondidas[message.channel.id] = time.time()

    # 7. Decisión espontánea
    await decidir_participar_espontaneo(message.channel, bot, message)


# --- Evento on_command_error ---
@bot.event
async def on_command_error(ctx, error):
    import traceback as tb

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title=" Sin Permisos",
            description="No tienes permisos para usar este comando.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed, delete_after=8)
        return

    if isinstance(error, commands.NotOwner):
        await ctx.send(" Este comando es solo para el dueño del bot.", delete_after=5)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title=" Argumento Faltante",
            description=f"Falta el argumento: `{error.param.name}`",
            color=discord.Color.orange()
        )
        if ctx.command:
            embed.add_field(
                name="Uso",
                value=f"`¿{ctx.command.qualified_name} {ctx.command.signature}`",
                inline=False
            )
        await ctx.send(embed=embed, delete_after=15)
        return

    if isinstance(error, commands.BadArgument):
        embed = discord.Embed(
            title=" Argumento Inválido",
            description=str(error)[:300],
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed, delete_after=10)
        return

    architect_cog = bot.get_cog("ArchitectCog")
    if architect_cog:
        try:
            await architect_cog.handle_error_diagnosis(ctx, error)
        except Exception as e:
            print(f"Error en auto-diagnóstico: {e}")

    original = getattr(error, 'original', error)
    print(f" Error en comando '{ctx.command}': {original}")
    tb.print_exception(type(original), original, original.__traceback__)

    embed = discord.Embed(
        title=" ¡Ups! Algo salió mal",
        description="Ha ocurrido un error interno al ejecutar este comando.",
        color=discord.Color.red()
    )
    embed.set_footer(text="El Arquitecto de Pomposo ha sido notificado con los detalles y está reparándolo.")
    await ctx.send(embed=embed)


# --- Comandos de Bloqueo con Fuzzy Matching ---
@bot.command()
@commands.is_owner()
async def block(ctx, *, user_query: str):
    try:
        if ctx.message.mentions:
            member = ctx.message.mentions[0]
        elif user_query.isdigit():
            member_id = int(user_query)
            member = ctx.guild.get_member(member_id)
            if not member:
                return await ctx.send(f" No encontré ningún miembro con ID `{member_id}` en este servidor.")
        else:
            matches = find_member_fuzzy(ctx.guild, user_query)
            if not matches:
                return await ctx.send(f" No encontré ningún usuario similar a: **{user_query}**")
            if len(matches) > 1:
                embed = discord.Embed(
                    title=" Múltiples coincidencias encontradas",
                    description=f"Encontré varios usuarios similares a **{user_query}**:",
                    color=discord.Color.orange()
                )
                for i, (m, score) in enumerate(matches[:5], 1):
                    embed.add_field(
                        name=f"{i}. {m.display_name}",
                        value=f"`{m.name}` (ID: `{m.id}`) - Similitud: {score}%",
                        inline=False
                    )
                embed.set_footer(text="Usa ¿block @usuario o ¿block ID para especificar")
                return await ctx.send(embed=embed)
            member, score = matches[0]
            await ctx.send(f" Usuario encontrado: **{member.display_name}** (similitud: {score}%)")

        if member.id == bot.owner_id:
            return await ctx.send(" No puedes bloquearte a ti mismo.")
        if member.id in blocked_user_ids:
            return await ctx.send(f" {member.mention} ya está bloqueado.")

        blocked_user_ids.add(member.id)
        save_blocked_users()

        embed = discord.Embed(
            title=" Usuario Bloqueado",
            description=f"{member.mention} ha sido bloqueado exitosamente.",
            color=discord.Color.red()
        )
        embed.add_field(name="Usuario", value=member.name, inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f" Error al bloquear usuario: {e}")


@bot.command()
@commands.is_owner()
async def unblock(ctx, *, user_query: str):
    try:
        if ctx.message.mentions:
            member = ctx.message.mentions[0]
        elif user_query.isdigit():
            member_id = int(user_query)
            member = ctx.guild.get_member(member_id)
            if not member:
                return await ctx.send(f" No encontré ningún miembro con ID `{member_id}` en este servidor.")
        else:
            matches = find_member_fuzzy(ctx.guild, user_query)
            if not matches:
                return await ctx.send(f" No encontré ningún usuario similar a: **{user_query}**")
            if len(matches) > 1:
                embed = discord.Embed(
                    title=" Múltiples coincidencias encontradas",
                    description=f"Encontré varios usuarios similares a **{user_query}**:",
                    color=discord.Color.orange()
                )
                for i, (m, score) in enumerate(matches[:5], 1):
                    status = " Bloqueado" if m.id in blocked_user_ids else " No bloqueado"
                    embed.add_field(
                        name=f"{i}. {m.display_name} {status}",
                        value=f"`{m.name}` (ID: `{m.id}`) - Similitud: {score}%",
                        inline=False
                    )
                embed.set_footer(text="Usa ¿unblock @usuario o ¿unblock ID para especificar")
                return await ctx.send(embed=embed)
            member, score = matches[0]
            await ctx.send(f" Usuario encontrado: **{member.display_name}** (similitud: {score}%)")

        if member.id not in blocked_user_ids:
            return await ctx.send(f" {member.mention} no está en la lista de bloqueados.")

        blocked_user_ids.remove(member.id)
        save_blocked_users()

        embed = discord.Embed(
            title=" Usuario Desbloqueado",
            description=f"{member.mention} ha sido desbloqueado exitosamente.",
            color=discord.Color.green()
        )
        embed.add_field(name="Usuario", value=member.name, inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f" Error al desbloquear usuario: {e}")


@bot.command()
@commands.is_owner()
async def blocklist(ctx):
    if not blocked_user_ids:
        return await ctx.send(" La lista de bloqueados está vacía.")

    embed = discord.Embed(
        title=" Lista de Usuarios Bloqueados",
        description=f"Total: {len(blocked_user_ids)} usuario(s)",
        color=discord.Color.red()
    )

    blocked_list = []
    for user_id in blocked_user_ids:
        try:
            user = bot.get_user(user_id)
            if not user:
                user = await bot.fetch_user(user_id)
            blocked_list.append(f"• **{user.name}** (ID: `{user_id}`)")
        except:
            blocked_list.append(f"• Usuario Desconocido (ID: `{user_id}`)")

    chunk_size = 10
    for i in range(0, len(blocked_list), chunk_size):
        chunk = blocked_list[i:i + chunk_size]
        embed.add_field(
            name=f"Usuarios {i + 1}-{min(i + chunk_size, len(blocked_list))}",
            value="\n".join(chunk),
            inline=False
        )

    await ctx.send(embed=embed)


# --- Comando Decir ---
@bot.command(name="decir")
@commands.is_owner()
async def decir_command(ctx, channel: discord.TextChannel, *, message: str):
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send("No tengo permisos para borrar mensajes.", ephemeral=True)
    await channel.send(message)


@bot.command()
@commands.is_owner()
async def sync(ctx, type: str = None):
    msg = await ctx.send(" Sincronizando...")
    try:
        if type == "guild":
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await msg.edit(content=f" Sincronizados {len(synced)} comandos en este servidor (Instantáneo).")
        elif type == "clear":
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            await msg.edit(content=" Comandos globales borrados.")
        else:
            synced = await bot.tree.sync()
            await msg.edit(content=f" Sincronizados {len(synced)} comandos globales.")
    except Exception as e:
        await msg.edit(content=f" Error: {e}")


# --- Ejecución del Bot ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print(" ERROR: No se encontró la variable 'DISCORD_TOKEN' en el archivo .env.")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            print(" ERROR: El token de Discord no es válido.")
        except Exception as e:
            print(f" Ocurrió un error inesperado al iniciar el bot: {e}")