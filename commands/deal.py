import os
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import Button, View
import aiohttp
from dotenv import load_dotenv

# --- Configuración Inicial ---
load_dotenv()
BASE_URL = "https://api.isthereanydeal.com"
# HARDCODED KEY AS REQUESTED BY USER
API_KEY = "10bf57a763592072886082e399845b818eead15c"

def get_api_key():
    """
    Retorna la API key hardcodeada.
    """
    return API_KEY

# --- Vistas (UI) ---
class GameSelectionView(View):
    """Vista para seleccionar un juego cuando hay múltiples resultados."""
    
    def __init__(self, games, cog, ctx, timeout=60):
        super().__init__(timeout=timeout)
        self.games = games
        self.cog = cog
        self.ctx = ctx
        self.selected_game = None
        
        # Crear botones para los primeros 3 juegos (User requested limit 3)
        for i, game in enumerate(games[:3]):
            button = Button(
                label=f"{i+1}",
                style=discord.ButtonStyle.primary,
                custom_id=f"game_{i}"
            )
            button.callback = self.create_callback(i)
            self.add_item(button)
        
        # Botón de cancelar
        cancel_button = Button(
            label="Cancelar",
            style=discord.ButtonStyle.danger,
            custom_id="cancel",
            emoji="❌"
        )
        cancel_button.callback = self.cancel_callback
        self.add_item(cancel_button)
    
    def create_callback(self, index):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("Solo quien usó el comando puede seleccionar.", ephemeral=True)
                return
            
            self.selected_game = self.games[index]
            
            # Editar el mensaje original con la nueva información
            # Primero obtenemos el embed de ofertas
            embed = await self.cog.create_deal_embed(self.selected_game)
            
            # Actualizamos el mensaje reemplazando embed y quitando botones
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
        return callback
    
    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Solo quien usó el comando puede cancelar.", ephemeral=True)
            return
        
        await interaction.response.send_message("Búsqueda cancelada.", ephemeral=True)
        # Deshabilitar botones en vez de borrarlos para mostrar que se canceló
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        self.stop()
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(content="⏱️ Tiempo agotado.", view=self)
        except:
            pass

# --- Cog Principal ---
class DealCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def search_game(self, title: str, limit: int = 3): # Limit default to 3
        """Busca juegos por título en la API."""
        api_key = get_api_key()
        if not api_key:
            print("⚠️ API Key no encontrada en search_game")
            return None

        url = f"{BASE_URL}/games/search/v1"
        params = {
            "key": api_key,
            "title": title, 
            "limit": limit
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        print(f"❌ Error API Search ({resp.status}): {await resp.text()}")
                        return None
        except Exception as e:
            print(f"❌ Excepción en search_game: {e}")
            return None

    async def get_prices(self, game_id: str, country: str = "US"):
        """Obtiene precios actuales para un juego."""
        api_key = get_api_key()
        if not api_key:
            return None

        url = f"{BASE_URL}/games/prices/v3"
        params = {
            "key": api_key,
            "country": country, 
            "deals_only": "true", 
            "limit": 10
        }
        headers = {"Content-Type": "application/json"}
        body = [game_id]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, params=params, headers=headers, json=body) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and len(data) > 0 and "deals" in data[0]:
                            deals = data[0]["deals"]
                            deals.sort(key=lambda x: x.get("cut", 0), reverse=True)
                            return deals
                    else:
                        print(f"❌ Error API Prices ({resp.status}): {await resp.text()}")
                    return []
        except Exception as e:
            print(f"❌ Excepción en get_prices: {e}")
            return []

    async def create_deal_embed(self, game):
        """Helper para crear el embed de ofertas."""
        game_title = game.get("title", "Juego desconocido")
        game_id = game.get("id")
        
        deals = await self.get_prices(game_id)
        
        if not deals:
            return discord.Embed(
                title=f"🎮 {game_title}",
                description="📭 No encontré ofertas actuales para este juego.",
                color=discord.Color.light_grey()
            )

        embed = discord.Embed(
            title=f"🎮 Ofertas para: {game_title}",
            color=discord.Color.green(),
            description="Mejores precios encontrados:"
        )
        
        medals = ["🥇", "🥈", "🥉"]
        
        for i, deal in enumerate(deals[:5]): # Mostrar top 5
            shop = deal.get("shop", {}).get("name", "Tienda")
            price = deal.get("price", {}).get("amount", 0)
            currency = deal.get("price", {}).get("currency", "$")
            regular = deal.get("regular", {}).get("amount", 0)
            cut = deal.get("cut", 0)
            url = deal.get("url", "")
            
            symbol = "$" if currency == "USD" else currency + " "
            medal = medals[i] if i < len(medals) else "🏷️"
            
            field_name = f"{medal} {shop}"
            field_value = (
                f"**{symbol}{price:.2f}** ~~{symbol}{regular:.2f}~~ "
                f"(-{cut}%)\n"
                f"[Ver Oferta]({url})"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)
        
        embed.set_footer(text="Powered by IsThereAnyDeal")
        return embed

    async def show_deals(self, ctx, game):
        """Método legacy para compatibilidad o uso directo."""
        embed = await self.create_deal_embed(game)
        await ctx.send(embed=embed)

    @commands.command(name="deal", description="Busca ofertas de juegos")
    async def deal(self, ctx, *, game_title: str):
        """
        Busca ofertas para un juego.
        Uso: ¿deal <nombre del juego>
        """
        if not get_api_key():
            await ctx.send("⚠️ Error de configuración: API Key no encontrada.")
            return

        async with ctx.typing():
            # Limit search to 3 as requested
            games = await self.search_game(game_title, limit=3)
            
            if not games:
                await ctx.send(f"❌ No encontré juegos que coincidan con: **{game_title}**")
                return
            
            # Coincidencia exacta o único resultado
            if len(games) == 1:
                await self.show_deals(ctx, games[0])
                return
            
            # Múltiples resultados -> Selección
            embed = discord.Embed(
                title="🔍 Selección de Juego",
                description=f"Encontré varios resultados para **{game_title}**. Selecciona uno:",
                color=discord.Color.blue()
            )
            
            for i, game in enumerate(games[:3]):
                embed.add_field(
                    name=f"{i+1}. {game.get('title')}",
                    value="_ _", # Empty value as requested
                    inline=False
                )
            
            embed.set_footer(text="Usa los botones para seleccionar un juego 👇")
            
            view = GameSelectionView(games, self, ctx)
            view.message = await ctx.send(embed=embed, view=view)

    @app_commands.command(name="deal", description="Busca ofertas de un juego")
    @app_commands.describe(juego="Nombre del juego")
    async def deal_slash(self, interaction: discord.Interaction, juego: str):
        """Slash command para deal."""
        await self.deal(await self.bot.get_context(interaction), game_title=juego)


async def setup(bot):
    await bot.add_cog(DealCog(bot))
    print("✅ Module commands.deal loaded (Rewritten Version)")
