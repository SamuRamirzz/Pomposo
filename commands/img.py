# commands/img.py

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import random
from typing import List, Optional

BRAVE_API_KEY = os.getenv("BRAVE_API_SEARCH", "")
BRAVE_IMAGE_URL = "https://api.search.brave.com/res/v1/images/search"

MAX_RESULTS = 20
URL_CHECK_TIMEOUT = 4


class ImageResult:
    """Representa un resultado de imagen."""

    def __init__(self, url: str, thumbnail: str, title: str, context: str, width: int = 0, height: int = 0):
        self.url = url
        self.thumbnail = thumbnail
        self.title = title
        self.context = context
        self.width = width
        self.height = height


class ImageNavigator(discord.ui.View):
    """Vista con botones de navegación para las imágenes."""

    def __init__(self, images: List[ImageResult], query: str, author_id: int):
        super().__init__(timeout=300)
        self.images = images
        self.query = query
        self.current_index = 0
        self.author_id = author_id

        self.link_button = discord.ui.Button(
            label=" Abrir",
            style=discord.ButtonStyle.link,
            url=images[0].url if images else "https://search.brave.com"
        )
        self.add_item(self.link_button)
        self.update_buttons()

    def update_link_button(self):
        if self.images and self.current_index < len(self.images):
            self.remove_item(self.link_button)
            self.link_button = discord.ui.Button(
                label=" Abrir",
                style=discord.ButtonStyle.link,
                url=self.images[self.current_index].url
            )
            self.add_item(self.link_button)

    def update_buttons(self):
        self.previous_button.disabled = (self.current_index == 0)
        self.next_button.disabled = (self.current_index >= len(self.images) - 1)
        self.update_link_button()

    def build_embed(self) -> discord.Embed:
        if not self.images or self.current_index >= len(self.images):
            return discord.Embed(
                title=" Error",
                description="No hay imágenes disponibles.",
                color=discord.Color.red()
            )

        img = self.images[self.current_index]

        embed = discord.Embed(
            title=f" {self.query}",
            description=img.title[:200] if img.title else None,
            color=discord.Color.blue()
        )
        embed.set_image(url=img.url)

        if img.context:
            embed.add_field(name=" Fuente", value=f"[Ver página]({img.context})"[:1024], inline=True)

        if img.width and img.height:
            embed.add_field(name=" Resolución", value=f"{img.width}×{img.height}", inline=True)

        embed.set_footer(text=f"Imagen {self.current_index + 1}/{len(self.images)} •   para navegar")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                " Solo quien ejecutó el comando puede usar estos botones.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="", style=discord.ButtonStyle.gray, custom_id="prev")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index > 0:
            self.current_index -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="", style=discord.ButtonStyle.blurple, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="", style=discord.ButtonStyle.gray, custom_id="shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.images) > 1:
            new_index = self.current_index
            while new_index == self.current_index:
                new_index = random.randint(0, len(self.images) - 1)
            self.current_index = new_index
            self.update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="", style=discord.ButtonStyle.red, custom_id="delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()

    async def on_timeout(self):
        for item in self.children:
            if not isinstance(item, discord.ui.Button) or item.style != discord.ButtonStyle.link:
                item.disabled = True


class ImageSearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def validate_image_url(self, session: aiohttp.ClientSession, url: str) -> bool:
        """HEAD request rápido para verificar que la imagen es accesible."""
        try:
            async with session.head(
                url,
                timeout=aiohttp.ClientTimeout(total=URL_CHECK_TIMEOUT),
                allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    return False
                content_type = resp.headers.get("Content-Type", "")
                return content_type.startswith("image/")
        except Exception:
            return False

    async def search_images_brave(self, query: str) -> List[ImageResult]:
        """
        Busca imágenes usando la API de Brave Search.
        Sin safesearch — devuelve resultados sin filtros.
        """
        if not BRAVE_API_KEY:
            raise ValueError("Falta `BRAVE_API_SEARCH` en el .env")

        session = await self._get_session()

        params = {
            "q": query,
            "count": MAX_RESULTS,
            "safesearch": "off",    # Sin filtros
            "search_lang": "es",
            "country": "CO",
        }
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": BRAVE_API_KEY,
        }

        async with session.get(
            BRAVE_IMAGE_URL,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 401:
                raise ValueError("API key de Brave inválida o expirada.")
            if resp.status == 429:
                raise ValueError("Límite de requests de Brave alcanzado. Espera un momento.")
            if resp.status != 200:
                raise ValueError(f"Brave devolvió HTTP {resp.status}")

            data = await resp.json()

        raw_results: List[ImageResult] = []
        seen_urls: set = set()

        for item in data.get("results", []):
            url = item.get("url") or item.get("properties", {}).get("url")
            if not url or url in seen_urls:
                continue

            # Filtrar URLs inútiles
            if any(bad in url.lower() for bad in [".svg", "data:image", "placeholder"]):
                continue

            seen_urls.add(url)

            thumbnail = item.get("thumbnail", {}).get("src", url)
            title = item.get("title", "Sin título")
            context = item.get("source", "") or item.get("page_url", "")

            # Dimensiones (Brave las incluye a veces)
            props = item.get("properties", {})
            width = props.get("width", 0)
            height = props.get("height", 0)

            raw_results.append(ImageResult(url, thumbnail, title, context, width, height))

            if len(raw_results) >= MAX_RESULTS:
                break

        if not raw_results:
            return []

        # Validar URLs en paralelo
        import asyncio
        validation_tasks = [self.validate_image_url(session, img.url) for img in raw_results]
        validations = await asyncio.gather(*validation_tasks, return_exceptions=True)

        validated = [img for img, ok in zip(raw_results, validations) if ok is True]

        # Fallback: si validan menos de 3, devolver sin validar
        if len(validated) < 3:
            return raw_results

        return validated

    async def execute_search(self, query: str, context) -> None:
        """Ejecuta la búsqueda y muestra los resultados con el navegador."""
        is_interaction = isinstance(context, discord.Interaction)

        if is_interaction:
            await context.response.defer()

        if not query or not query.strip():
            error_msg = " Debes especificar qué buscar.\n**Ejemplo:** `¿img gatos espaciales`"
            if is_interaction:
                await context.followup.send(error_msg)
            else:
                await context.reply(error_msg)
            return

        query = query.strip()

        if is_interaction:
            loading = await context.followup.send(f" Buscando imágenes de **{query}**...")
        else:
            loading = await context.reply(f" Buscando imágenes de **{query}**...")

        try:
            images = await self.search_images_brave(query)
        except ValueError as e:
            embed = discord.Embed(
                title=" Error en la búsqueda",
                description=str(e),
                color=discord.Color.red()
            )
            await loading.edit(content="", embed=embed)
            return
        except Exception as e:
            embed = discord.Embed(
                title=" Error inesperado",
                description=f"```{str(e)[:300]}```",
                color=discord.Color.red()
            )
            await loading.edit(content="", embed=embed)
            return

        if not images:
            embed = discord.Embed(
                title=" Sin resultados",
                description=f"No encontré imágenes para: **{query}**\n\nIntenta con otros términos.",
                color=discord.Color.orange()
            )
            await loading.edit(content="", embed=embed)
            return

        author_id = context.user.id if is_interaction else context.author.id
        navigator = ImageNavigator(images, query, author_id)
        await loading.edit(content="", embed=navigator.build_embed(), view=navigator)

    # --- COMANDO TRADICIONAL ---
    @commands.command(name="img", aliases=["image", "imagen"])
    async def img_command(self, ctx, *, query: str = ""):
        """
        Busca imágenes con Brave Search.

        Uso: ¿img <término de búsqueda>
        Ejemplo: ¿img gatos espaciales
        """
        await self.execute_search(query, ctx)

    # --- SLASH COMMAND ---
    @app_commands.command(name="img", description="Busca imágenes con Brave Search")
    @app_commands.describe(query="¿Qué quieres buscar?")
    async def img_slash(self, interaction: discord.Interaction, query: str):
        await self.execute_search(query, interaction)


async def setup(bot):
    await bot.add_cog(ImageSearchCog(bot))
    print(" ImageSearchCog (Brave) cargado.")