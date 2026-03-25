import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import random
import os
from fuzzywuzzy import fuzz, process


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


class TouchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tenor_api_key = os.getenv('TENOR_API_KEY')
        self.giphy_api_key = os.getenv('GIPHY_API_KEY')
        self.tenor_base_url = "https://tenor.googleapis.com/v2/search"
        self.giphy_base_url = "https://api.giphy.com/v1/gifs/search"
        
        # Términos de búsqueda variados para más diversidad (Acariciar/Tocar)
        self.search_terms = [
            "petting cat",
            "petting dog",
            "head pat",
            "animal petting",
            "petting kitten",
            "petting bunny",
            "cat head pat",
            "dog head pat",
            "petting puppy"
        ]

    async def get_gif_from_tenor(self, search_term: str) -> str:
        """Obtiene un GIF desde Tenor API."""
        if not self.tenor_api_key:
            return None

        try:
            params = {
                "q": search_term,
                "key": self.tenor_api_key,
                "limit": 30,
                "contentfilter": "medium",
                "media_filter": "gif"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(self.tenor_base_url, params=params, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("results"):
                            gif = random.choice(data["results"])
                            return gif["media_formats"]["gif"]["url"]
        except Exception as e:
            print(f" Error obteniendo GIF de Tenor: {e}")
        
        return None

    async def get_gif_from_giphy(self, search_term: str) -> str:
        """Obtiene un GIF desde Giphy API."""
        if not self.giphy_api_key:
            return None

        try:
            params = {
                "q": search_term,
                "api_key": self.giphy_api_key,
                "limit": 30,
                "rating": "pg"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(self.giphy_base_url, params=params, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("data"):
                            gif = random.choice(data["data"])
                            return gif["images"]["original"]["url"]
        except Exception as e:
            print(f" Error obteniendo GIF de Giphy: {e}")
        
        return None

    async def get_touch_gif(self) -> str:
        """
        Obtiene un GIF aleatorio de animales siendo acariciados.
        """
        # Seleccionar término de búsqueda aleatorio
        search_term = random.choice(self.search_terms)
        
        # Alternar aleatoriamente entre APIs disponibles
        sources = []
        if self.tenor_api_key:
            sources.append(('tenor', self.get_gif_from_tenor))
        if self.giphy_api_key:
            sources.append(('giphy', self.get_gif_from_giphy))
        
        # Si no hay APIs configuradas, usar fallback
        if not sources:
            print(" No hay APIs configuradas (TENOR_API_KEY o GIPHY_API_KEY)")
            return self._get_fallback_gif()
        
        # Mezclar las fuentes para alternar aleatoriamente
        random.shuffle(sources)
        
        # Intentar obtener GIF de cada fuente
        for source_name, source_func in sources:
            gif_url = await source_func(search_term)
            if gif_url:
                print(f" GIF obtenido de {source_name.upper()} con término: '{search_term}'")
                return gif_url
        
        # Si todas las APIs fallan, usar fallback
        print(" Todas las APIs fallaron, usando GIF de respaldo")
        return self._get_fallback_gif()

    def _get_fallback_gif(self) -> str:
        """Retorna un GIF de respaldo si las APIs fallan."""
        fallback_gifs = [
            "https://media.tenor.com/F_N6S15iUeAAAAAM/cat-petting.gif",
            "https://media.tenor.com/T_49v-IizkIAAAAM/dog-petting.gif",
            "https://media.tenor.com/I7V6eN8G2nQAAAAM/cat-head-pat.gif",
            "https://media.tenor.com/N74D6E-g7xAAAAAM/bunny-petting.gif"
        ]
        return random.choice(fallback_gifs)

    @commands.command(name="tocar")
    async def tocar_command(self, ctx, *, user_query: str):
        """
        Toca a alguien

        Uso: ¿tocar <nombre/mención/ID>
        Ejemplos:
            ¿tocar @Usuario
            ¿tocar 123456789
            ¿tocar juan (busca usuarios similares a "juan")
        """
        try:
            # Intentar parsear como mención o ID
            target_member = None

            # Si es una mención
            if ctx.message.mentions:
                target_member = ctx.message.mentions[0]
            # Si es un ID directo
            elif user_query.isdigit():
                member_id = int(user_query)
                target_member = ctx.guild.get_member(member_id)
                if not target_member:
                    return await ctx.send(f"No encontré ningún miembro con ID `{member_id}` en este servidor.")
            # Si es un nombre, usar fuzzy matching
            else:
                matches = find_member_fuzzy(ctx.guild, user_query)

                if not matches:
                    return await ctx.send(
                        f"No encontré ningún usuario similar a: **{user_query}**\n Intenta con el nombre exacto o una mención."
                    )

                # Seleccionar el usuario con mayor similitud
                target_member, score = matches[0]

            # Validaciones
            if target_member.id == ctx.author.id:
                return await ctx.send("oye que ")

            if target_member.id == self.bot.user.id:
                return await ctx.send("no me TOQUES")

            # Obtener GIF
            gif_url = await self.get_touch_gif()

            # Crear embed
            embed = discord.Embed(
                title="...",
                description=f"fuiste TOCADO, **{target_member.display_name}...**!",
                color=discord.Color.from_rgb(255, 182, 193) # Rosa pastel
            )
            embed.set_image(url=gif_url)
            embed.set_footer(
                text=f"{ctx.author.name}",
                icon_url=ctx.author.display_avatar.url
            )

            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f" Error al ejecutar el comando: {e}")

    @app_commands.command(name="tocar", description="toca a alguien")
    @app_commands.describe(usuario="El usuario que vas a tocar")
    async def tocar_slash(self, interaction: discord.Interaction, usuario: discord.Member):
        """Comando slash para tocar."""
        
        # Validaciones
        if usuario.id == interaction.user.id:
            return await interaction.response.send_message("A kien vas a tokar", ephemeral=False)

        if usuario.id == self.bot.user.id:
            return await interaction.response.send_message("Para...", ephemeral=False)

        # Defer para tener tiempo de obtener el GIF
        await interaction.response.defer()

        # Obtener GIF
        gif_url = await self.get_touch_gif()

        # Crear embed
        embed = discord.Embed(
            title="...",
            description=f"Fuiste TOCADO, **{usuario.display_name}...**!",
            color=discord.Color.from_rgb(255, 182, 193)
        )
        embed.set_image(url=gif_url)
        embed.set_footer(
            text=f"{interaction.user.name}",
            icon_url=interaction.user.display_avatar.url
        )

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TouchCog(bot))
    print(" TouchCog (commands.tocar) cargado correctamente.")
