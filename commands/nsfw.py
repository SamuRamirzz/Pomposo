
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import random
import os
from fuzzywuzzy import fuzz, process


# --- Funciones de Fuzzy Matching (Reutilizadas para consistencia) ---
def find_member_fuzzy(guild: discord.Guild, query: str, threshold: int = 40) -> list:
    """Busca miembros en el servidor usando fuzzy matching."""
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
        if score >= threshold:
            member = member_names[match_name]
            if member.id not in seen_ids:
                results.append((member, score))
                seen_ids.add(member.id)

    return results


class NsfwCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Endpoint específico de PurrBot (Gratis y sin Key)
        self.purrbot_api_url = "https://purrbot.site/api/img/nsfw/fuck/gif"

    async def get_nsfw_gif(self) -> str:
        """Obtiene un GIF NSFW desde PurrBot API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.purrbot_api_url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if not data.get("error"):
                            return data.get("link")
        except Exception as e:
            print(f"⚠️ Error PurrBot: {e}")
        
        # Fallback por si la API falla
        return "https://media.tenor.com/F_N6S15iUeAAAAAM/cat-petting.gif"

    @commands.command(name="nsfw")
    async def nsfw_command(self, ctx, *, user_query: str):
        """Interactúa de forma explícita (Solo canales NSFW)"""
        try:
            target_member = None
            # Prioridad de búsqueda: Mención > ID > Fuzzy Match
            if ctx.message.mentions:
                target_member = ctx.message.mentions[0]
            elif user_query.isdigit():
                member_id = int(user_query)
                target_member = ctx.guild.get_member(member_id)
            else:
                matches = find_member_fuzzy(ctx.guild, user_query)
                if matches:
                    target_member = matches[0][0]

            if not target_member:
                return await ctx.send(f"No encontré a nadie con: **{user_query}**")

            if target_member.id == self.bot.owner_id:
                return await ctx.send("❌ No puedes hacer eso con mi creador... ten más respeto. 😤")
            
            if target_member.id == ctx.author.id:
                return await ctx.send("¿Contigo mismo? Mejor busca a alguien más... 🙄")
            
            gif_url = await self.get_nsfw_gif()
            
            embed = discord.Embed(
                description=f"**{ctx.author.display_name}** está teniendo sexo con **{target_member.display_name}** 🥵",
                color=discord.Color.dark_red()
            )
            embed.set_image(url=gif_url)
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    @app_commands.command(name="nsfw", description="Comando NSFW explícito")
    @app_commands.describe(usuario="Usuario con el que interactuar")
    async def nsfw_slash(self, interaction: discord.Interaction, usuario: discord.Member):

            
        if usuario.id == self.bot.owner_id:
            return await interaction.response.send_message("❌ No puedes hacer eso con mi creador... ten más respeto. 😤", ephemeral=True)
            
        if usuario.id == interaction.user.id:
            return await interaction.response.send_message("¿Contigo mismo? ...", ephemeral=True)

        await interaction.response.defer()
        gif_url = await self.get_nsfw_gif()
        
        embed = discord.Embed(
            description=f"**{interaction.user.display_name}** está teniendo sexo con **{usuario.display_name}** 🥵",
            color=discord.Color.dark_red()
        )
        embed.set_image(url=gif_url)
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(NsfwCog(bot))
    print("✅ NsfwCog cargado.")
