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

load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

_app = Flask(__name__)

BLOCKED_USERS_FILE = 'blocked_users.json'
CONFIG_FILE = 'bot_config.json'
blocked_user_ids = set()
FUZZY_THRESHOLD = 60

logger = logging.getLogger("pomposo")

# ─────────────────────────────────────────────
# Configuración autónoma
# ─────────────────────────────────────────────
POMPOSO_CONFIG = {
    "cooldown_segundos": 120,
    "min_mensajes_activo": 3,
    "prob_base": 0.08,
    "prob_chat_activo": 0.12,
    "prob_palabras_clave": 0.08,
    "prob_no_respondido": 0.10,
    "penalizacion_spam": 0.05,
    "max_mensajes_10min": 3,
}

# ─────────────────────────────────────────────
# Estado global
# ─────────────────────────────────────────────
_cooldown_canales: dict[int, float] = {}
_actividad_canales: dict[int, list[float]] = defaultdict(list)
_mensajes_pomposo: dict[int, list[float]] = defaultdict(list)
_menciones_no_respondidas: dict[int, float] = {}
_cache_analisis: dict[int, bool] = {}

# Anti-spam: {user_id: [timestamps de comandos]}
_spam_tracker: dict[int, list[float]] = defaultdict(list)
# Usuarios en cooldown temporal: {user_id: timestamp_expira}
_spam_cooldown: dict[int, float] = {}

SPAM_MAX_COMANDOS = 5      # máximo de comandos
SPAM_VENTANA = 10          # en X segundos
SPAM_BLOQUEO = 30          # bloqueo temporal en segundos

# {user_id: {"timestamp": float, "channel_id": int, "bot_message": discord.Message, "pregunta_original": str}}
# Cuando Pomposo responde, guarda el mensaje por si llega un follow-up
_respuesta_editable: dict[int, dict] = {}
CONTEXTO_VENTANA = 8       # segundos para detectar el mensaje de seguimiento


def registrar_actividad(channel_id: int):
    ahora = time.time()
    _actividad_canales[channel_id].append(ahora)
    _actividad_canales[channel_id] = [t for t in _actividad_canales[channel_id] if ahora - t < 300]


def mensajes_en_ultimo_minuto(channel_id: int) -> int:
    ahora = time.time()
    return sum(1 for t in _actividad_canales[channel_id] if ahora - t < 60)


def segundos_desde_ultimo_mensaje_pomposo(channel_id: int) -> float:
    if channel_id not in _cooldown_canales:
        return float('inf')
    return time.time() - _cooldown_canales[channel_id]


def mensajes_pomposo_en_10min(channel_id: int) -> int:
    ahora = time.time()
    _mensajes_pomposo[channel_id] = [t for t in _mensajes_pomposo[channel_id] if ahora - t < 600]
    return len(_mensajes_pomposo[channel_id])


def registrar_mensaje_pomposo(channel_id: int):
    ahora = time.time()
    _cooldown_canales[channel_id] = ahora
    _mensajes_pomposo[channel_id].append(ahora)
    if len(_cache_analisis) > 300:
        _cache_analisis.clear()


def check_spam(user_id: int) -> bool:
    """
    Verifica si el usuario está haciendo spam de comandos.
    Retorna True si está bloqueado por spam, False si puede continuar.
    """
    ahora = time.time()

    # Verificar si está en cooldown activo
    if user_id in _spam_cooldown:
        if ahora < _spam_cooldown[user_id]:
            return True  # sigue bloqueado
        else:
            del _spam_cooldown[user_id]  # expiró, limpiar

    # Registrar este comando y limpiar viejos
    _spam_tracker[user_id].append(ahora)
    _spam_tracker[user_id] = [t for t in _spam_tracker[user_id] if ahora - t < SPAM_VENTANA]

    # Verificar si superó el límite
    if len(_spam_tracker[user_id]) >= SPAM_MAX_COMANDOS:
        _spam_cooldown[user_id] = ahora + SPAM_BLOQUEO
        _spam_tracker[user_id].clear()
        return True

    return False


def menciona_a_pomposo(contenido: str) -> bool:
    """Detecta si el mensaje menciona a Pomposo por nombre."""
    contenido = contenido.lower().strip()
    return any(p in contenido for p in ["pomposo", "pomposito", "pomposi"])


# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────
@_app.route("/")
def health():
    return "Pomposo activo", 200

def _run_server():
    _app.run(host="0.0.0.0", port=8080)

threading.Thread(target=_run_server, daemon=True).start()


# ─────────────────────────────────────────────
# Config y bloqueo
# ─────────────────────────────────────────────
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_blocked_users():
    global blocked_user_ids
    try:
        with open(BLOCKED_USERS_FILE, 'r') as f:
            blocked_list = json.load(f)
            blocked_user_ids = set(blocked_list)
            print(f"Cargados {len(blocked_user_ids)} usuarios bloqueados.")
    except FileNotFoundError:
        blocked_user_ids = set()
    except json.JSONDecodeError:
        blocked_user_ids = set()


def save_blocked_users():
    with open(BLOCKED_USERS_FILE, 'w') as f:
        json.dump(list(blocked_user_ids), f)


# ─────────────────────────────────────────────
# Bot
# ─────────────────────────────────────────────
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


def find_member_fuzzy(guild: discord.Guild, query: str) -> list:
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


# ─────────────────────────────────────────────
# on_ready
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Conectado como {bot.user}!")
    load_blocked_users()
    config = load_config()
    bot.auto_reply_channel_id = config.get("auto_channel_id")
    if bot.auto_reply_channel_id:
        print(f"Canal de auto-respuesta: {bot.auto_reply_channel_id}")
    if not bot.owner_id:
        app_info = await bot.application_info()
        bot.owner_id = app_info.owner.id
        print(f"Owner: {app_info.owner.name} (ID: {bot.owner_id})")
    await load_all_cogs()
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizados {len(synced)} comandos de barra.")
    except Exception as e:
        print(f"Error al sincronizar comandos: {e}")


async def load_all_cogs():
    print("--- Cargando Cogs ---")
    if not os.path.exists('./commands'):
        return
    for filename in os.listdir('./commands'):
        if filename.endswith('.py') and filename != '__init__.py':
            cog_name = f'commands.{filename[:-3]}'
            try:
                await bot.load_extension(cog_name)
                print(f"Cargado: {cog_name}")
            except Exception as e:
                print(f"Error cargando {cog_name}: {e}")
                import traceback
                traceback.print_exc()
    print("---------------------")


# ─────────────────────────────────────────────
# Detector semántico
# ─────────────────────────────────────────────
async def me_estan_hablando(message: discord.Message, bot_instance) -> bool:
    if bot_instance.user in message.mentions:
        return True
    if (
        message.reference is not None
        and message.reference.resolved is not None
        and isinstance(message.reference.resolved, discord.Message)
        and message.reference.resolved.author.id == bot_instance.user.id
    ):
        return True

    if message.id in _cache_analisis:
        return _cache_analisis[message.id]

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
            model="z-ai/glm-4.5-air:free",  # modelo ligero para clasificación
            temperature=0.0,
            max_tokens=5
        )
        resultado = bool(respuesta) and respuesta.strip().upper().startswith("SI")
        _cache_analisis[message.id] = resultado
        if resultado:
            logger.info(f"Pomposo detectado como destinatario en #{message.channel.name}")
        return resultado
    except Exception as e:
        logger.error(f"Error en me_estan_hablando: {e}")
        return False


# ─────────────────────────────────────────────
# Mensaje espontáneo
# ─────────────────────────────────────────────
async def generar_mensaje_espontaneo(channel: discord.TextChannel, bot_instance):
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

    prompt_espontaneo = (
        f"{personalidad}\n\n"
        "Estás viendo esta conversación en tu servidor de Discord y decidiste meterte porque te dio la gana:\n\n"
        f"{historial_canal}\n\n"
        "Escribe UN mensaje corto con tu personalidad. Máximo 2 oraciones. "
        "No saludes, no digas 'oigan' ni 'hey', entra directo. Sé impredecible."
    )

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
            logger.info(f"Pomposo espontáneo en #{channel.name}: {respuesta.strip()[:60]}")
    except Exception as e:
        logger.error(f"Error en generar_mensaje_espontaneo: {e}")


# ─────────────────────────────────────────────
# Decisión autónoma
# ─────────────────────────────────────────────
async def decidir_participar_espontaneo(channel: discord.TextChannel, bot_instance, message: discord.Message):
    cfg = POMPOSO_CONFIG
    cid = channel.id

    if segundos_desde_ultimo_mensaje_pomposo(cid) < cfg["cooldown_segundos"]:
        return

    msgs_minuto = mensajes_en_ultimo_minuto(cid)
    if msgs_minuto < cfg["min_mensajes_activo"]:
        return

    prob = cfg["prob_base"]
    if msgs_minuto > 8:
        prob += cfg["prob_chat_activo"]

    palabras_clave = {"jeje", "sorra", "ijo", "pereza", "gei", "xd", "mon dieu", "q asco", "pomposo"}
    mensaje_lower = (message.content or "").lower()
    if any(pw in mensaje_lower for pw in palabras_clave):
        prob += cfg["prob_palabras_clave"]

    if cid in _menciones_no_respondidas:
        if time.time() - _menciones_no_respondidas[cid] < 300:
            prob += cfg["prob_no_respondido"]

    if mensajes_pomposo_en_10min(cid) >= cfg["max_mensajes_10min"]:
        prob -= cfg["penalizacion_spam"]

    prob = max(0.0, min(1.0, prob))

    if random.random() < prob:
        logger.info(f"Pomposo decide hablar en #{channel.name} (prob: {prob:.0%})")
        await generar_mensaje_espontaneo(channel, bot_instance)


# ─────────────────────────────────────────────
# on_message
# ─────────────────────────────────────────────
@bot.event
async def on_message(message):
    if isinstance(message.channel, discord.DMChannel) and message.author != bot.user:
        print(f"\n[DM de {message.author.name}]: {message.content}")
        print("-" * 40)

    if message.author.bot:
        return

    if message.author.id in blocked_user_ids:
        return

    if not isinstance(message.channel, discord.DMChannel):
        registrar_actividad(message.channel.id)

    # Menea tu chapa
    message_lower = (message.content or "").lower()
    if "menea" in message_lower and "chapa" in message_lower:
        try:
            await message.channel.send(file=discord.File("chapa.mp3"))
        except Exception:
            pass

    # Anti-spam: verificar antes de procesar comandos
    if (message.content or "").startswith(bot.command_prefix):
        if message.author.id != bot.owner_id and check_spam(message.author.id):
            segundos_restantes = int(_spam_cooldown.get(message.author.id, time.time()) - time.time())
            try:
                await message.reply(
                    f"para con el spam 🙄 espera {segundos_restantes}s",
                    delete_after=10
                )
            except Exception:
                pass
            return

    await bot.process_commands(message)

    if (message.content or "").startswith(bot.command_prefix):
        return

    if isinstance(message.channel, discord.DMChannel):
        return

    if (message.content or "").lower().startswith('¿decir ') and message.author.id == bot.owner_id:
        return

    # Canal de auto-respuesta fijo
    if hasattr(bot, 'auto_reply_channel_id') and message.channel.id == bot.auto_reply_channel_id:
        ask_cog = bot.get_cog("AskCog")
        if ask_cog:
            try:
                ctx = await bot.get_context(message)
                await ask_cog.handle_ask(ctx, pregunta=message.content)
                registrar_mensaje_pomposo(message.channel.id)
            except Exception as e:
                print(f"Error en auto-ask: {e}")
        return

    uid = message.author.id
    ahora = time.time()

    # ── Follow-up: si Pomposo respondió hace poco a este usuario, editar con contexto nuevo ──
    if uid in _respuesta_editable:
        entry = _respuesta_editable[uid]
        if (
            ahora - entry["timestamp"] <= CONTEXTO_VENTANA
            and entry["channel_id"] == message.channel.id
            and message.content.strip()
            and not (message.content or "").startswith(bot.command_prefix)
        ):
            # Hay un follow-up — pedir a la IA una respuesta nueva con contexto completo
            ask_cog = bot.get_cog("AskCog")
            if ask_cog:
                pregunta_completa = f"{entry['pregunta_original']} {message.content.strip()}"
                ctx = await bot.get_context(message)
                del _respuesta_editable[uid]  # limpiar antes de responder
                try:
                    # Editar el mensaje anterior de Pomposo con la nueva respuesta
                    bot_msg = entry["bot_message"]
                    nueva_resp = await ask_cog.get_response(ctx, pregunta_completa)
                    if nueva_resp and bot_msg:
                        await bot_msg.edit(content=nueva_resp)
                    elif nueva_resp:
                        await ctx.reply(nueva_resp)
                except Exception:
                    # Si no se puede editar, responder normal
                    await ask_cog.handle_ask(ctx, pregunta=message.content.strip())
                registrar_mensaje_pomposo(message.channel.id)
                _menciones_no_respondidas.pop(message.channel.id, None)
            return
        else:
            del _respuesta_editable[uid]

    # ── Detectar si le hablan a Pomposo ──
    if await me_estan_hablando(message, bot) or menciona_a_pomposo(message.content or ""):
        ask_cog = bot.get_cog("AskCog")
        if ask_cog:
            ctx = await bot.get_context(message)
            bot_msg = await ask_cog.handle_ask(ctx, pregunta=message.content)
            registrar_mensaje_pomposo(message.channel.id)
            _menciones_no_respondidas.pop(message.channel.id, None)
            # Guardar la respuesta para posible follow-up
            if bot_msg:
                _respuesta_editable[uid] = {
                    "timestamp": ahora,
                    "channel_id": message.channel.id,
                    "bot_message": bot_msg,
                    "pregunta_original": message.content.strip()
                }
        return

    if bot.user in message.mentions:
        _menciones_no_respondidas[message.channel.id] = time.time()

    await decidir_participar_espontaneo(message.channel, bot, message)


# ─────────────────────────────────────────────
# on_command_error — un solo mensaje de error
# ─────────────────────────────────────────────
@bot.event
async def on_command_error(ctx, error):
    import traceback as tb

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=discord.Embed(
            title="Sin Permisos",
            description="No tienes permisos para usar este comando.",
            color=discord.Color.red()
        ), delete_after=8)
        return

    if isinstance(error, commands.NotOwner):
        await ctx.send("Este comando es solo para el dueño.", delete_after=5)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="Argumento Faltante",
            description=f"Falta: `{error.param.name}`",
            color=discord.Color.orange()
        )
        if ctx.command:
            embed.add_field(name="Uso", value=f"`¿{ctx.command.qualified_name} {ctx.command.signature}`")
        await ctx.send(embed=embed, delete_after=15)
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(embed=discord.Embed(
            title="Argumento Inválido",
            description=str(error)[:300],
            color=discord.Color.orange()
        ), delete_after=10)
        return

    # Para errores reales: derivar al arquitecto Y mandar UN solo mensaje
    original = getattr(error, 'original', error)
    print(f"Error en '{ctx.command}': {original}")
    tb.print_exception(type(original), original, original.__traceback__)

    architect_cog = bot.get_cog("ArchitectCog")
    arquitecto_notifico = False
    if architect_cog:
        try:
            await architect_cog.handle_error_diagnosis(ctx, error)
            arquitecto_notifico = True
        except Exception as e:
            print(f"Error en auto-diagnóstico: {e}")

    # Solo mandar el embed genérico si el arquitecto NO notificó (para no duplicar)
    if not arquitecto_notifico:
        await ctx.send(embed=discord.Embed(
            title="Ups, algo salió mal",
            description="Ocurrió un error interno.",
            color=discord.Color.red()
        ))


# ─────────────────────────────────────────────
# Comandos de bloqueo
# ─────────────────────────────────────────────
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
                return await ctx.send(f"No encontré ID `{member_id}`.")
        else:
            matches = find_member_fuzzy(ctx.guild, user_query)
            if not matches:
                return await ctx.send(f"No encontré: **{user_query}**")
            if len(matches) > 1:
                embed = discord.Embed(title="Múltiples coincidencias", color=discord.Color.orange())
                for i, (m, score) in enumerate(matches[:5], 1):
                    embed.add_field(name=f"{i}. {m.display_name}", value=f"`{m.name}` ({score}%)", inline=False)
                embed.set_footer(text="Usa ¿block @usuario o ID para especificar")
                return await ctx.send(embed=embed)
            member, score = matches[0]
            await ctx.send(f"Usuario encontrado: **{member.display_name}** ({score}%)")

        if member.id == bot.owner_id:
            return await ctx.send("No puedes bloquearte a ti mismo.")
        if member.id in blocked_user_ids:
            return await ctx.send(f"{member.mention} ya está bloqueado.")

        blocked_user_ids.add(member.id)
        save_blocked_users()
        embed = discord.Embed(title="Usuario Bloqueado", description=f"{member.mention} bloqueado.", color=discord.Color.red())
        embed.add_field(name="Usuario", value=member.name, inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error: {e}")


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
                return await ctx.send(f"No encontré ID `{member_id}`.")
        else:
            matches = find_member_fuzzy(ctx.guild, user_query)
            if not matches:
                return await ctx.send(f"No encontré: **{user_query}**")
            if len(matches) > 1:
                embed = discord.Embed(title="Múltiples coincidencias", color=discord.Color.orange())
                for i, (m, score) in enumerate(matches[:5], 1):
                    status = "Bloqueado" if m.id in blocked_user_ids else "No bloqueado"
                    embed.add_field(name=f"{i}. {m.display_name} {status}", value=f"`{m.name}` ({score}%)", inline=False)
                embed.set_footer(text="Usa ¿unblock @usuario o ID para especificar")
                return await ctx.send(embed=embed)
            member, score = matches[0]
            await ctx.send(f"Usuario encontrado: **{member.display_name}** ({score}%)")

        if member.id not in blocked_user_ids:
            return await ctx.send(f"{member.mention} no está bloqueado.")

        blocked_user_ids.remove(member.id)
        save_blocked_users()
        embed = discord.Embed(title="Usuario Desbloqueado", description=f"{member.mention} desbloqueado.", color=discord.Color.green())
        embed.add_field(name="Usuario", value=member.name, inline=True)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error: {e}")


@bot.command()
@commands.is_owner()
async def blocklist(ctx):
    if not blocked_user_ids:
        return await ctx.send("La lista de bloqueados está vacía.")
    embed = discord.Embed(title="Usuarios Bloqueados", description=f"Total: {len(blocked_user_ids)}", color=discord.Color.red())
    blocked_list = []
    for user_id in blocked_user_ids:
        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            blocked_list.append(f"• **{user.name}** (`{user_id}`)")
        except Exception:
            blocked_list.append(f"• Desconocido (`{user_id}`)")
    for i in range(0, len(blocked_list), 10):
        embed.add_field(name=f"{i+1}-{min(i+10, len(blocked_list))}", value="\n".join(blocked_list[i:i+10]), inline=False)
    await ctx.send(embed=embed)


@bot.command(name="decir")
@commands.is_owner()
async def decir_command(ctx, channel: discord.TextChannel, *, message: str):
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    await channel.send(message)


@bot.command()
@commands.is_owner()
async def sync(ctx, type: str = None):
    msg = await ctx.send("Sincronizando...")
    try:
        if type == "guild":
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await msg.edit(content=f"Sincronizados {len(synced)} comandos en este servidor.")
        elif type == "clear":
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            await msg.edit(content="Comandos globales borrados.")
        else:
            synced = await bot.tree.sync()
            await msg.edit(content=f"Sincronizados {len(synced)} comandos globales.")
    except Exception as e:
        await msg.edit(content=f"Error: {e}")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: No se encontró DISCORD_TOKEN en el .env.")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            print("ERROR: Token inválido.")
        except Exception as e:
            print(f"Error inesperado: {e}")