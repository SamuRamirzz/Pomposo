
import os
import json
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from fuzzywuzzy import fuzz, process

# --- Carga de Secretos ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# --- Constantes y Configuración ---
BLOCKED_USERS_FILE = 'blocked_users.json'
CONFIG_FILE = 'bot_config.json'
blocked_user_ids = set()

# Umbral de similitud para fuzzy matching (0-100)
FUZZY_THRESHOLD = 60


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

# Deshabilitar el comando help por defecto
bot = commands.Bot(
    command_prefix='¿',
    intents=intents,
    owner_id=None,
    case_insensitive=True,
    help_command=None  # Deshabilita el help por defecto
)


# --- Función de Búsqueda Fuzzy ---
def find_member_fuzzy(guild: discord.Guild, query: str) -> list:
    """
    Busca miembros en el servidor usando fuzzy matching.
    Retorna lista de tuplas: [(miembro, score), ...]
    """
    if not guild:
        return []

    # Crear diccionario de nombres -> miembros
    member_names = {}
    for member in guild.members:
        # Buscar por nombre de usuario
        member_names[member.name.lower()] = member
        # Buscar por display name (apodo en el servidor)
        if member.display_name != member.name:
            member_names[member.display_name.lower()] = member
        # Buscar por nombre#discriminador (si existe)
        if member.discriminator != "0":
            member_names[f"{member.name}#{member.discriminator}".lower()] = member

    # Buscar coincidencias usando fuzzy matching
    query_lower = query.lower()
    matches = process.extract(query_lower, member_names.keys(), scorer=fuzz.ratio, limit=5)

    # Filtrar por umbral y devolver miembros únicos
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


# --- Evento on_message ---
@bot.event
async def on_message(message):
    """Se ejecuta cada vez que se envía un mensaje en un canal que el bot puede ver."""

    # 1. LÓGICA DE MD (Mensaje Directo) A LA CONSOLA
    if isinstance(message.channel, discord.DMChannel) and message.author != bot.user:
        print(f"\n[ DM de {message.author.name}]: {message.content}")

        if message.attachments:
            print("   [ Imagen/Archivo adjunto detectado]:")
            for attachment in message.attachments:
                print(f"    LINK: {attachment.url}")

        print("-" * 40)

    # 2. IGNORAR MENSAJES DEL BOT
    if message.author == bot.user:
        return

    if message.author.id in blocked_user_ids:
        return

    # LÓGICA DE "MENEA TU CHAPA"
    message_lower = message.content.lower()
    if "menea" in message_lower and "chapa" in message_lower:
        try:
            chapa_file = discord.File("chapa.mp3")
            await message.channel.send(file=chapa_file)
        except FileNotFoundError:
            print(" Error: No se encontró el archivo chapa.mp3")
        except Exception as e:
            print(f" Error al enviar chapa.mp3: {e}")

    # LÓGICA DE AUTO-RESPUESTA (Canal de IA)
    if hasattr(bot, 'auto_reply_channel_id') and message.channel.id == bot.auto_reply_channel_id:
        if not message.content.startswith(bot.command_prefix):
            ask_cog = bot.get_cog("AskCog")
            if ask_cog:
                try:
                    ctx = await bot.get_context(message)
                    await ask_cog.ask.callback(ask_cog, ctx, pregunta=message.content)
                except Exception as e:
                    print(f"Error al invocar auto-ask: {e}")
            return

    # Comando ¿decir de dueño
    if message.content.lower().startswith('¿decir ') and message.author.id == bot.owner_id:
        try:
            await bot.process_commands(message)
        except Exception as e:
            print(f"Error al procesar comando 'decir': {e}")
        return

    await bot.process_commands(message)


# --- Evento on_command_error (Auto-Diagnóstico V2) ---
@bot.event
async def on_command_error(ctx, error):
    """
    Captura errores de comandos con manejo inteligente.
    - Errores comunes: respuesta directa con embeds
    - Errores de código: derivados al Arquitecto para auto-reparación
    """
    import traceback as tb

    # Ignorar comandos no encontrados
    if isinstance(error, commands.CommandNotFound):
        return
    
    # Manejar errores comunes con embeds
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
    
    # Para otros errores, derivar al Arquitecto para diagnóstico inteligente
    architect_cog = bot.get_cog("ArchitectCog")
    if architect_cog:
        try:
            await architect_cog.handle_error_diagnosis(ctx, error)
        except Exception as e:
            print(f"Error en auto-diagnóstico: {e}")
    
    # Imprimir en consola
    original = getattr(error, 'original', error)
    print(f" Error en comando '{ctx.command}': {original}")
    tb.print_exception(type(original), original, original.__traceback__)
    
    # Notificar al usuario con embed
    # Notificar al usuario con embed
    embed = discord.Embed(
        title=" ¡Ups! Algo salió mal",
        description="Ha ocurrido un error interno al ejecutar este comando.",
        color=discord.Color.red()
    )
    # Ya no enviamos el "str(original)" por privacidad, 
    # el Arquitecto igualmente lo leerá y registrará en la carpeta staging/.
    embed.set_footer(text="El Arquitecto de Pomposo ha sido notificado con los detalles y está reparándolo.")
    await ctx.send(embed=embed)


# --- Comandos de Bloqueo MEJORADOS con Fuzzy Matching ---
@bot.command()
@commands.is_owner()
async def block(ctx, *, user_query: str):
    """
    Bloquea a un usuario usando fuzzy matching.

    Uso: ¿block <nombre/mención/ID>
    Ejemplos:
        ¿block @Usuario
        ¿block 123456789
        ¿block juan (busca usuarios similares a "juan")
    """
    # Intentar parsear como mención o ID
    try:
        # Si es una mención
        if ctx.message.mentions:
            member = ctx.message.mentions[0]
        # Si es un ID directo
        elif user_query.isdigit():
            member_id = int(user_query)
            member = ctx.guild.get_member(member_id)
            if not member:
                return await ctx.send(f" No encontré ningún miembro con ID `{member_id}` en este servidor.")
        # Si es un nombre, usar fuzzy matching
        else:
            matches = find_member_fuzzy(ctx.guild, user_query)

            if not matches:
                return await ctx.send(
                    f" No encontré ningún usuario similar a: **{user_query}**\n Intenta con el nombre exacto o una mención.")

            # Si hay múltiples coincidencias, mostrar opciones
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

            # Una sola coincidencia
            member, score = matches[0]
            await ctx.send(f" Usuario encontrado: **{member.display_name}** (similitud: {score}%)")

        # Validaciones
        if member.id == bot.owner_id:
            return await ctx.send(" No puedes bloquearte a ti mismo.")

        if member.id in blocked_user_ids:
            return await ctx.send(f" {member.mention} ya está bloqueado.")

        # Bloquear usuario
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
    """
    Desbloquea a un usuario usando fuzzy matching.

    Uso: ¿unblock <nombre/mención/ID>
    Ejemplos:
        ¿unblock @Usuario
        ¿unblock 123456789
        ¿unblock juan (busca usuarios similares a "juan")
    """
    try:
        # Si es una mención
        if ctx.message.mentions:
            member = ctx.message.mentions[0]
        # Si es un ID directo
        elif user_query.isdigit():
            member_id = int(user_query)
            member = ctx.guild.get_member(member_id)
            if not member:
                return await ctx.send(f" No encontré ningún miembro con ID `{member_id}` en este servidor.")
        # Si es un nombre, usar fuzzy matching
        else:
            matches = find_member_fuzzy(ctx.guild, user_query)

            if not matches:
                return await ctx.send(
                    f" No encontré ningún usuario similar a: **{user_query}**\n Intenta con el nombre exacto o una mención.")

            # Si hay múltiples coincidencias
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

            # Una sola coincidencia
            member, score = matches[0]
            await ctx.send(f" Usuario encontrado: **{member.display_name}** (similitud: {score}%)")

        # Validar que esté bloqueado
        if member.id not in blocked_user_ids:
            return await ctx.send(f" {member.mention} no está en la lista de bloqueados.")

        # Desbloquear usuario
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
    """Muestra la lista de usuarios bloqueados."""
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

    # Dividir en chunks si hay muchos usuarios
    chunk_size = 10
    for i in range(0, len(blocked_list), chunk_size):
        chunk = blocked_list[i:i + chunk_size]
        embed.add_field(
            name=f"Usuarios {i + 1}-{min(i + chunk_size, len(blocked_list))}",
            value="\n".join(chunk),
            inline=False
        )

    await ctx.send(embed=embed)


# --- Comando Decir (Comando de Dueño) ---
@bot.command(name="decir")
@commands.is_owner()
async def decir_command(ctx, channel: discord.TextChannel, *, message: str):
    """Hace que el bot hable en un canal específico, borrando el mensaje original."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send("No tengo permisos para borrar mensajes.", ephemeral=True)

    await channel.send(message)


@bot.command()
@commands.is_owner()
async def sync(ctx, type: str = None):
    """
    Sincroniza los comandos slash.
    Uso:
    ¿sync -> Sincronización global (lenta, ~1h)
    ¿sync guild -> Sincronización inmediata en este servidor
    ¿sync clear -> Borra los comandos globales
    """
    msg = await ctx.send(" Sincronizando...")
    try:
        if type == "guild":
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await msg.edit(content=f" Sincronizados {len(synced)} comandos en este servidor (Instantáneo).")
        elif type == "clear":
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            await msg.edit(content=" Comandos globales borrados. Reinicia Discord para ver los cambios.")
        else:
            synced = await bot.tree.sync()
            await msg.edit(content=f" Sincronizados {len(synced)} comandos globales. Puede tardar hasta 1h en actualizarse.")
            
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