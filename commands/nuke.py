import discord
from discord.ext import commands
import asyncio

class Moderacion(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="nuke")
    @commands.is_owner() # nunk kites esto o drake se enoja
    async def nuke(self, ctx):
        """
        Recrea el canal para borrar todo el historial.
        Solo clones, no geis.
        """
        try:
            # cojemos el canal biego
            canal_sucio = ctx.channel
            posicion = canal_sucio.position # pa q no se pierda el lugar jeje

            # clonamos el canal tal cual esta
            canal_limpio = await canal_sucio.clone(reason="Limpieza estilo Pomposo")

            # nos aseguramos q este en la misma posicion
            await canal_limpio.edit(position=posicion)

            # aora si borramos el biego a la chingada
            # esto solo borra UNA VEZ, asi q no ai doble delete pemdejo
            await canal_sucio.delete()

            # Y no mandamos mensaje al final como pediste
            # todo silencioso shhh 🤫

        except Exception as e:
            # si falla pos nimodo, avisamos en consola
            print(f"Oye drake algo salio mal en el nuke: {e}")

async def setup(bot):
    await bot.add_cog(Moderacion(bot))