import discord
from discord.ext import commands
from discord import app_commands
from fuzzywuzzy import fuzz, process
from typing import Optional


# --- Funciones de Fuzzy Matching ---
def find_member_fuzzy(guild: discord.Guild, query: str, threshold: int = 40) -> list:
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
        if score >= threshold:
            member = member_names[match_name]
            if member.id not in seen_ids:
                results.append((member, score))
                seen_ids.add(member.id)

    return results


class NicknameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def change_nickname(self, guild: discord.Guild, member: discord.Member, new_nick: str, executor: discord.Member) -> tuple[bool, str]:
        """
        Cambia el nickname de un miembro.
        
        Returns:
            tuple[bool, str]: (éxito, mensaje)
        """
        # Validaciones
        if len(new_nick) > 32:
            return False, " El apodo no puede tener más de 32 caracteres."
        
        if len(new_nick.strip()) == 0:
            return False, " El apodo no puede estar vacío."

        # Verificar si el bot tiene permisos
        bot_member = guild.get_member(self.bot.user.id)
        if not bot_member.guild_permissions.manage_nicknames:
            return False, " No tengo permisos para cambiar apodos (necesito `Manage Nicknames`)."

        # Verificar jerarquía de roles
        if member.top_role >= bot_member.top_role and guild.owner_id != self.bot.user.id:
            return False, f" No puedo cambiar el apodo de {member.mention} porque su rol es igual o superior al mío."

        # No se puede cambiar el apodo del dueño del servidor
        if member.id == guild.owner_id:
            return False, f" No puedo cambiar el apodo del dueño del servidor."

        # Guardar el apodo anterior
        old_nick = member.display_name

        try:
            await member.edit(nick=new_nick, reason=f"Cambiado por {executor.name}")
            return True, f" Apodo cambiado exitosamente:\n**{old_nick}** → **{new_nick}**"
        except discord.Forbidden:
            return False, f" No tengo permisos para cambiar el apodo de {member.mention}."
        except discord.HTTPException as e:
            return False, f" Error al cambiar el apodo: {e}"

    @commands.command(name="nick")
    async def nick_command(self, ctx, user_query: str, *, new_nickname: str):
        """
        Cambia el apodo de un usuario usando fuzzy matching.

        Uso: ¿nick <nombre/mención/ID> <nuevo apodo>
        Ejemplos:
            ¿nick @Usuario NuevoApodo
            ¿nick 123456789 NuevoApodo
            ¿nick juan Juanito (busca usuarios similares a "juan")
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
                        f" No encontré ningún usuario similar a: **{user_query}**\n Intenta con el nombre exacto o una mención."
                    )

                # Seleccionar el usuario con mayor similitud (el primero de la lista)
                member, score = matches[0]
                await ctx.send(f" Usuario encontrado: **{member.display_name}** (similitud: {score}%)")

            # Cambiar el nickname
            success, message = await self.change_nickname(ctx.guild, member, new_nickname, ctx.author)
            
            if success:
                embed = discord.Embed(
                    title=" Apodo Cambiado",
                    description=message,
                    color=discord.Color.green()
                )
                embed.add_field(name="Usuario", value=member.mention, inline=True)
                embed.add_field(name="Nuevo Apodo", value=f"`{new_nickname}`", inline=True)
                embed.add_field(name="Cambiado por", value=ctx.author.mention, inline=True)
                embed.set_thumbnail(url=member.display_avatar.url)
                await ctx.send(embed=embed)
            else:
                await ctx.send(message)

        except commands.MissingRequiredArgument:
            await ctx.send(" Uso correcto: `¿nick <usuario> <nuevo apodo>`")
        except Exception as e:
            await ctx.send(f" Error al cambiar apodo: {e}")

    @app_commands.command(name="nick", description="Cambia el apodo de un usuario")
    @app_commands.describe(
        usuario="El usuario al que quieres cambiar el apodo",
        nuevo_apodo="El nuevo apodo que quieres asignar"
    )
    async def nick_slash(self, interaction: discord.Interaction, usuario: discord.Member, nuevo_apodo: str):
        """Cambia el apodo de un usuario usando slash command (efímero)."""
        
        # Respuesta efímera para que solo el ejecutor la vea
        await interaction.response.defer(ephemeral=True)

        # Cambiar el nickname
        success, message = await self.change_nickname(interaction.guild, usuario, nuevo_apodo, interaction.user)
        
        if success:
            embed = discord.Embed(
                title=" Apodo Cambiado",
                description=message,
                color=discord.Color.green()
            )
            embed.add_field(name="Usuario", value=usuario.mention, inline=True)
            embed.add_field(name="Nuevo Apodo", value=f"`{nuevo_apodo}`", inline=True)
            embed.add_field(name="Cambiado por", value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=usuario.display_avatar.url)
            embed.set_footer(text=" Este mensaje solo es visible para ti")
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

    @nick_command.error
    async def nick_command_error(self, ctx, error):
        """Maneja errores del comando de prefijo."""
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(" Uso correcto: `¿nick <usuario> <nuevo apodo>`")

    @nick_slash.error
    async def nick_slash_error(self, interaction: discord.Interaction, error):
        """Maneja errores del slash command."""
        pass  # Sin restricciones de permisos


async def setup(bot):
    await bot.add_cog(NicknameCog(bot))
