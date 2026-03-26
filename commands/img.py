import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import asyncio
import random
from typing import List, Optional

# Configuración de búsqueda
MAX_RESULTS = 20          # Resultados totales a obtener
RESULTS_PER_PAGE = 10     # Máximo por request de la API
URL_CHECK_TIMEOUT = 4     # Segundos para validar cada URL
RANDOM_START_RANGE = 10   # Rango de randomización del start index


class ImageResult:
    """Representa un resultado de imagen validado."""

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
        super().__init__(timeout=300)  # 5 minutos
        self.images = images
        self.query = query
        self.current_index = 0
        self.author_id = author_id

        # Botón de link dinámico
        self.link_button = discord.ui.Button(
            label=" Abrir",
            style=discord.ButtonStyle.link,
            url=images[0].url if images else "https://google.com"
        )
        self.add_item(self.link_button)
        self.update_buttons()

    def update_link_button(self):
        """Actualiza la URL del botón de link."""
        if self.images and self.current_index < len(self.images):
            self.remove_item(self.link_button)
            self.link_button = discord.ui.Button(
                label=" Abrir",
                style=discord.ButtonStyle.link,
                url=self.images[self.current_index].url
            )
            self.add_item(self.link_button)

    def update_buttons(self):
        """Actualiza el estado de los botones según la posición actual."""
        self.previous_button.disabled = (self.current_index == 0)
        self.next_button.disabled = (self.current_index >= len(self.images) - 1)
        self.update_link_button()

    def build_embed(self) -> discord.Embed:
        """Construye el embed con la imagen actual."""
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

        # Imagen principal (URL completa, no thumbnail)
        embed.set_image(url=img.url)

        # Fuente
        if img.context:
            embed.add_field(
                name=" Fuente",
                value=f"[Ver página]({img.context})"[:1024],
                inline=True
            )

        # Resolución si está disponible
        if img.width and img.height:
            embed.add_field(
                name=" Resolución",
                value=f"{img.width}×{img.height}",
                inline=True
            )

        # Footer con posición
        embed.set_footer(
            text=f"Imagen {self.current_index + 1}/{len(self.images)} •   para navegar"
        )

        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el autor del comando puede usar los botones."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                " Solo quien ejecutó el comando puede usar estos botones.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="", style=discord.ButtonStyle.gray, custom_id="prev")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ir a la imagen anterior."""
        if self.current_index > 0:
            self.current_index -= 1
            self.update_buttons()
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="", style=discord.ButtonStyle.blurple, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ir a la siguiente imagen."""
        if self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.update_buttons()
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="", style=discord.ButtonStyle.gray, custom_id="shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Saltar a una imagen aleatoria."""
        if len(self.images) > 1:
            new_index = self.current_index
            while new_index == self.current_index:
                new_index = random.randint(0, len(self.images) - 1)
            self.current_index = new_index
            self.update_buttons()
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="", style=discord.ButtonStyle.red, custom_id="delete")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Eliminar el mensaje."""
        await interaction.message.delete()

    async def on_timeout(self):
        """Deshabilitar botones cuando expire el timeout."""
        for item in self.children:
            if not isinstance(item, discord.ui.Button) or item.style != discord.ButtonStyle.link:
                item.disabled = True


class ImageSearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Reutilizar sesión HTTP para mejor rendimiento."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def cog_unload(self):
        """Cerrar sesión HTTP al descargar el cog."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def validate_image_url(self, session: aiohttp.ClientSession, url: str) -> bool:
        """
        Valida que una URL de imagen sea accesible.
        Hace un HEAD request rápido para verificar que la imagen existe.
        """
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=URL_CHECK_TIMEOUT),
                                     allow_redirects=True) as resp:
                if resp.status != 200:
                    return False
                # Verificar que sea realmente una imagen
                content_type = resp.headers.get('Content-Type', '')
                return content_type.startswith('image/')
        except:
            return False

    def _sync_ddgs_search(self, query: str, ddg_safe: str) -> List[dict]:
        """Wrapper síncrono para exécutar DDGS.images en un thread separado."""
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.images(
                keywords=query,
                region='wt-wt',
                safesearch=ddg_safe,
                max_results=MAX_RESULTS * 2  # Pedir el doble por si fallan validaciones
            ))

    async def search_images(self, query: str, safe_search: str = "on") -> List[ImageResult]:
        """
        Busca imágenes usando DuckDuckGo Search de forma gratuita (sin API keys).
        Corre en un thread separado para no bloquear el bot de Discord.
        """
        results = []
        try:
            # DuckDuckGo safesearch: 'on', 'moderate', 'off'
            ddg_safe = "on" if safe_search == "on" else "off"
            
            # Ejecutar la búsqueda sincrónica sin bloquear el event loop
            images = await asyncio.to_thread(self._sync_ddgs_search, query, ddg_safe)
            
            if not images:
                return []
                
            seen_urls = set()
            raw_results = []
            
            for item in images:
                image_url = item.get('image')
                if not image_url or image_url in seen_urls:
                    continue
                    
                seen_urls.add(image_url)
                thumbnail = item.get('thumbnail', image_url)
                title = item.get('title', 'Sin título')
                context = item.get('url', '')
                width = item.get('width', 0)
                height = item.get('height', 0)
                
                # Filtrar URLs rotas
                if any(bad in image_url.lower() for bad in [".svg", "data:image", "placeholder"]):
                    continue
                    
                raw_results.append(ImageResult(image_url, thumbnail, title, context, width, height))
                if len(raw_results) >= MAX_RESULTS:
                    break
                    
        except Exception as e:
            raise ValueError(f" Error con DuckDuckGo Search: {str(e)}")

        if not raw_results:
            return []

        # Validar las URLs
        session = await self._get_session()
        validation_tasks = [
            self.validate_image_url(session, img.url) 
            for img in raw_results
        ]
        validations = await asyncio.gather(*validation_tasks, return_exceptions=True)
        
        validated_results = []
        for img, is_valid in zip(raw_results, validations):
            if is_valid is True:
                validated_results.append(img)
                
        # Fallback si valida muy pocas
        if len(validated_results) < 3 and len(raw_results) > len(validated_results):
            return raw_results[:MAX_RESULTS]
            
        return validated_results

    async def execute_search(self, query: str, context) -> None:
        """Ejecuta la búsqueda y muestra los resultados."""
        is_interaction = isinstance(context, discord.Interaction)

        if is_interaction:
            await context.response.defer()

        # Validar query
        if not query or len(query.strip()) == 0:
            error_msg = " Debes especificar qué buscar.\n**Ejemplo:** `¿img gatos programando`"
            if is_interaction:
                await context.followup.send(error_msg)
            else:
                await context.reply(error_msg)
            return

        query = query.strip()

        # Determinar SafeSearch según el tipo de canal
        safe_search = "active"  # Por defecto: seguro
        channel = context.channel if not is_interaction else context.channel
        if hasattr(channel, 'nsfw') and channel.nsfw:
            safe_search = "off"

        # Mensaje de carga
        if is_interaction:
            loading = await context.followup.send(f" Buscando imágenes de **{query}**...")
        else:
            loading = await context.reply(f" Buscando imágenes de **{query}**...")

        # Realizar búsqueda
        try:
            images = await self.search_images(query, safe_search)
        except ValueError as e:
            error_embed = discord.Embed(
                title=" Error en la búsqueda",
                description=str(e),
                color=discord.Color.red()
            )
            await loading.edit(content="", embed=error_embed)
            return
        except Exception as e:
            error_embed = discord.Embed(
                title=" Error inesperado",
                description=f"Ocurrió un error: {str(e)[:300]}",
                color=discord.Color.red()
            )
            await loading.edit(content="", embed=error_embed)
            return

        # Sin resultados
        if not images:
            no_results = discord.Embed(
                title=" Sin resultados",
                description=f"No encontré imágenes para: **{query}**\n\nIntenta con otros términos.",
                color=discord.Color.orange()
            )
            await loading.edit(content="", embed=no_results)
            return

        # Crear navegador
        author_id = context.user.id if is_interaction else context.author.id
        navigator = ImageNavigator(images, query, author_id)

        # Mostrar primera imagen
        embed = navigator.build_embed()
        await loading.edit(content="", embed=embed, view=navigator)

    # --- COMANDO TRADICIONAL ---
    @commands.command(name="img", aliases=["image", "imagen"])
    async def img_command(self, ctx, *, query: str = ""):
        """
        Busca imágenes en Google.

        Uso: ¿img <término de búsqueda>
        Ejemplo: ¿img gatos espaciales
        """
        await self.execute_search(query, ctx)

    # --- SLASH COMMAND ---
    @app_commands.command(name="img", description="Busca imágenes en Google")
    @app_commands.describe(query="¿Qué quieres buscar?")
    async def img_slash(self, interaction: discord.Interaction, query: str):
        """Busca imágenes usando slash command."""
        await self.execute_search(query, interaction)


async def setup(bot):
    await bot.add_cog(ImageSearchCog(bot))
    print(" ImageSearchCog V2 cargado.")