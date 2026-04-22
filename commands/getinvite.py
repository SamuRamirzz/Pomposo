import discord
from discord.ext import commands
from discord import app_commands

class GetInviteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="getinvite", description="Obtiene la invitación de un servidor mediante su ID (Solo Owner)")
    @app_commands.describe(guild_id="El ID del servidor (opcional)")
    async def getinvite_slash(self, interaction: discord.Interaction, guild_id: str = None):
        if interaction.user.id != self.bot.owner_id:
            await interaction.response.send_message("No tienes permiso para usar este comando.", ephemeral=True)
            return
            
        await self._handle_getinvite(interaction, guild_id)

    @commands.command(name="getinvite", aliases=["serverinvite"], description="Obtiene la invitación de un servidor.")
    @commands.is_owner()
    async def getinvite_prefix(self, ctx, guild_id: str = None):
        await self._handle_getinvite(ctx, guild_id)

    async def _handle_getinvite(self, ctx_or_interaction, guild_id: str = None):
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        
        async def send_msg(content, ephemeral=False):
            if is_interaction:
                if not ctx_or_interaction.response.is_done():
                    await ctx_or_interaction.response.send_message(content, ephemeral=ephemeral)
                else:
                    await ctx_or_interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await ctx_or_interaction.send(content)

        if not guild_id:
            guilds_list = "\n".join([f"**{g.name}** (`{g.id}`)" for g in self.bot.guilds])
            if len(guilds_list) > 1900:
                guilds_list = guilds_list[:1800] + "\n... (lista trunca)"
            
            msg = (
                "**Servidores en los que estoy:**\n\n"
                f"{guilds_list}\n\n"
                "Usa `/getinvite <ID>` o `!getinvite <ID>` para generar un link."
            )
            await send_msg(msg, ephemeral=True)
            return

        try:
            guild_id_int = int(guild_id)
        except ValueError:
            await send_msg("ID de servidor inválido.", ephemeral=True)
            return

        guild = self.bot.get_guild(guild_id_int)
        if not guild:
            await send_msg("No estoy en ningún servidor con ese ID.", ephemeral=True)
            return

        # Intentar buscar un canal donde tengamos permisos para crear invitaciones
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).create_instant_invite:
                try:
                    invite = await channel.create_invite(max_age=86400, max_uses=1, unique=True, reason="Solicitado por el owner del bot")
                    await send_msg(f"**Invitación para {guild.name}:**\n{invite.url}", ephemeral=True)
                    return
                except Exception as e:
                    print(f"No se pudo crear invitación en {channel.name}: {e}")
                    continue
        
        await send_msg(f"No tengo permisos para crear invitaciones en **{guild.name}** o no hay canales de texto disponibles.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(GetInviteCog(bot))
    print(" Commands.getinvite cargado.")
