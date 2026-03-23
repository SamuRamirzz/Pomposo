import discord
import httpx
from discord.ext import commands

# Define la URL de TheCatAPI
CAT_API_URL = "https://api.thecatapi.com/v1/images/search"

# 1. Clase Cog
# Todos los comandos y eventos deben estar dentro de esta clase.
class ComandosGatos(commands.Cog):
    def __init__(self, bot):
        # El constructor recibe el objeto 'bot' de main.py
        self.bot = bot

    # 2. El comando híbrido "pomposo"
    @commands.hybrid_command(
        name="pomposo",
        description="veras un pomposito...",
        with_app_command=True
    )
    async def pomposo(self, ctx: commands.Context):
        """
        Este comando funciona como /pomposo y ¿pomposo.
        """
        
        await ctx.defer() # Aplaza la respuesta, esencial para comandos /slash.

        try:
            # Hacer la solicitud asíncrona a TheCatAPI
            async with httpx.AsyncClient() as client:
                response = await client.get(CAT_API_URL)
                response.raise_for_status() # Lanza un error si el código HTTP no es 2xx
                data = response.json()

            if data and len(data) > 0:
                image_url = data[0]['url']
                
                # Crear el Embed
                embed = discord.Embed(
                    title="Pomposito...",
                    color=discord.Color.blue()
                )
                embed.set_image(url=image_url)
                embed.set_footer(text="Imagen proporcionada por TheCatAPI ")
                
                await ctx.send(embed=embed)
                
            else:
                await ctx.send("No more pomposito...")

        except httpx.HTTPStatusError:
            await ctx.send("API ERROR")
        except Exception:
            await ctx.send("se crasheo pffff")


# 3. Función de Setup
# Esta función es requerida por discord.py para cargar el Cog.
async def setup(bot):
    # Añade una instancia de la clase al bot
    await bot.add_cog(ComandosGatos(bot))