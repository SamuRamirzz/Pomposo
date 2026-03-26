import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import asyncio
import random
from typing import List, Optional

# Usar GEMINI_API_KEY como fallback si no hay GOOGLE_SEARCH_API_KEY
GOOGLE_API_KEY = os.getenv('GOOGLE_SEARCH_API_KEY') or os.getenv('GEMINI_API_KEY')
GOOGLE_CX_ID = os.getenv('GOOGLE_SEARCH_CX_ID')

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

    async def search_images(self, query: str, safe_search: str = "active") -> List[ImageResult]:
        """
        Busca imágenes usando Google Custom Search API.
        
        Mejoras V2:
        - Sin restricción de imgSize (imágenes a resolución completa)
        - Randomización del start index para variedad
        - Doble request para obtener hasta 20 resultados
        - SafeSearch dinámico según el canal
        """
        if not GOOGLE_API_KEY or not GOOGLE_CX_ID:
            raise ValueError(
                " Faltan credenciales de Google Search API. Configura GOOGLE_SEARCH_API_KEY y GOOGLE_SEARCH_CX_ID")

        session = await self._get_session()
        all_items = []

        # Randomizar el punto de inicio para obtener resultados variados
        # Google permite start de 1 a 91 (límite de 100 resultados)
        random_start = random.randint(1, RANDOM_START_RANGE)

        # Hacer 2 requests para obtener hasta 20 resultados
        for page in range(2):
            start_index = random_start + (page * RESULTS_PER_PAGE)
            if start_index > 91:  # Límite de Google
                break

            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CX_ID,
                "q": query,
                "searchType": "image",
                "num": RESULTS_PER_PAGE,
                "start": start_index,
                "safe": safe_search,
                "filter": "1",      # Eliminar duplicados
                # NO imgSize → imágenes a resolución completa
                # NO imgType → todo tipo de imágenes
            }

            try:
                async with session.get(url, params=params) as response:
                    if response.status == 403:
                        raise ValueError(" API Key inválida o límite de cuota excedido.")
                    elif response.status == 400:
                        # Posible start index fuera de rango, intentar sin offset
                        if page == 0:
                            params["start"] = 1
                            async with session.get(url, params=params) as retry:
                                if retry.status == 200:
                                    data = await retry.json()
                                    all_items.extend(data.get("items", []))
                        continue
                    elif response.status != 200:
                        if page == 0:
                            raise ValueError(f" Error de API: {response.status}")
                        continue

                    data = await response.json()
                    items = data.get("items", [])
                    all_items.extend(items)

            except ValueError:
                raise
            except asyncio.TimeoutError:
                if page == 0:
                    raise ValueError(" Google tardó mucho en responder. Intenta de nuevo.")
                continue
            except aiohttp.ClientError as e:
                if page == 0:
                    raise ValueError(f" Error de conexión: {str(e)}")
                continue

        # Fallback: si no hubo resultados con start aleatorio, reintentar desde el inicio
        if not all_items and random_start > 1:
            try:
                params_fallback = {
                    "key": GOOGLE_API_KEY,
                    "cx": GOOGLE_CX_ID,
                    "q": query,
                    "searchType": "image",
                    "num": RESULTS_PER_PAGE,
                    "start": 1,
                    "safe": safe_search,
                    "filter": "1",
                }
                async with session.get("https://www.googleapis.com/customsearch/v1", params=params_fallback) as response:
                    if response.status == 200:
                        data = await response.json()
                        all_items.extend(data.get("items", []))
            except:
                pass

        if not all_items:
            return []

        # Procesar resultados
        results = []
        seen_urls = set()  # Evitar duplicados

        for item in all_items:
            try:
                image_url = item.get("link")
                if not image_url or image_url in seen_urls:
                    continue

                seen_urls.add(image_url)

                thumbnail = item.get("image", {}).get("thumbnailLink", image_url)
                title = item.get("title", "Sin título")
                context = item.get("image", {}).get("contextLink", "")
                width = item.get("image", {}).get("width", 0)
                height = item.get("image", {}).get("height", 0)

                # Filtrar URLs obviamente rotas
                if any(bad in image_url.lower() for bad in [".svg", "data:image", "placeholder"]):
                    continue

                results.append(ImageResult(image_url, thumbnail, title, context, width, height))
            except:
                continue

        # Validar las URLs de imagen en paralelo (las primeras 20)
        if results:
            validation_tasks = [
                self.validate_image_url(session, img.url) 
                for img in results[:MAX_RESULTS]
            ]
            validations = await asyncio.gather(*validation_tasks, return_exceptions=True)
            
            validated_results = []
            for img, is_valid in zip(results[:MAX_RESULTS], validations):
                if is_valid is True:
                    validated_results.append(img)
            
            # Si la validación filtró demasiadas, usar las no validadas como fallback
            if len(validated_results) < 3 and len(results) > len(validated_results):
                return results[:MAX_RESULTS]
            
            return validated_results

        return results

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