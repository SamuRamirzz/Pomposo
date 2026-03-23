import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import asyncio
from typing import List, Optional

# --- ⚙️ CONFIGURACIÓN ---
GOOGLE_API_KEY = os.getenv('GOOGLE_SEARCH_API_KEY')
GOOGLE_CX_ID = os.getenv('GOOGLE_SEARCH_CX_ID')
IMAGES_PER_PAGE = 10


class ImageResult:
    """Representa un resultado de imagen."""

    def __init__(self, url: str, thumbnail: str, title: str, context: str):
        self.url = url
        self.thumbnail = thumbnail
        self.title = title
        self.context = context


class ImageNavigator(discord.ui.View):
    """Vista con botones de navegación para las imágenes."""

    def __init__(self, images: List[ImageResult], query: str, author_id: int):
        super().__init__(timeout=300)  # 5 minutos
        self.images = images
        self.query = query
        self.current_index = 0
        self.author_id = author_id

        # Crear botón de link dinámicamente
        self.link_button = discord.ui.Button(
            label="🔗",
            style=discord.ButtonStyle.link,
            url=images[0].url if images else "https://google.com"
        )
        self.add_item(self.link_button)

        self.update_buttons()

    def update_link_button(self):
        """Actualiza la URL del botón de link."""
        if self.images and self.current_index < len(self.images):
            # Remover el botón anterior
            self.remove_item(self.link_button)

            # Crear nuevo botón con URL actualizada
            self.link_button = discord.ui.Button(
                label="🔗",
                style=discord.ButtonStyle.link,
                url=self.images[self.current_index].url
            )
            self.add_item(self.link_button)

    def update_buttons(self):
        """Actualiza el estado de los botones según la posición actual."""
        # Deshabilitar botón anterior si estamos en la primera imagen
        self.previous_button.disabled = (self.current_index == 0)

        # Deshabilitar botón siguiente si estamos en la última imagen
        self.next_button.disabled = (self.current_index >= len(self.images) - 1)

        # Actualizar botón de link a la imagen actual
        self.update_link_button()

    def build_embed(self) -> discord.Embed:
        """Construye el embed con la imagen actual."""
        if not self.images or self.current_index >= len(self.images):
            embed = discord.Embed(
                title="❌ Error",
                description="No hay imágenes disponibles.",
                color=discord.Color.red()
            )
            return embed

        img = self.images[self.current_index]

        embed = discord.Embed(
            title=f"🔍 Búsqueda: {self.query}",
            description=img.title[:256] if img.title else "Sin título",
            color=discord.Color.blue()
        )

        # Imagen principal
        embed.set_image(url=img.url)

        # Información adicional
        if img.context:
            embed.add_field(
                name="📄 Fuente",
                value=f"[Ver página]({img.context})"[:1024],
                inline=False
            )

        # Footer con posición
        embed.set_footer(
            text=f"Imagen {self.current_index + 1} de {len(self.images)} • Usa los botones para navegar"
        )

        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Verifica que solo el autor del comando pueda usar los botones."""
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Solo quien ejecutó el comando puede usar estos botones.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.gray, custom_id="prev")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ir a la imagen anterior."""
        if self.current_index > 0:
            self.current_index -= 1
            self.update_buttons()
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.blurple, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ir a la siguiente imagen."""
        if self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.update_buttons()
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="🗑️", style=discord.ButtonStyle.red, custom_id="delete")
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

    async def search_images(self, query: str) -> List[ImageResult]:
        """
        Busca imágenes usando Google Custom Search API.

        Args:
            query: Término de búsqueda

        Returns:
            Lista de ImageResult con las imágenes encontradas
        """
        if not GOOGLE_API_KEY or not GOOGLE_CX_ID:
            raise ValueError(
                "❌ Faltan credenciales de Google Search API. Configura GOOGLE_SEARCH_API_KEY y GOOGLE_SEARCH_CX_ID")

        print(f"🔍 Buscando imágenes: {query}")
        print(f"🔑 API Key: {GOOGLE_API_KEY[:10]}...")
        print(f"🆔 CX ID: {GOOGLE_CX_ID[:10]}...")

        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX_ID,
            "q": query,
            "searchType": "image",
            "num": IMAGES_PER_PAGE,
            "safe": "off",  # SafeSearch desactivado
            "imgSize": "medium",  # Tamaño medio para mejor compatibilidad
            "filter": "1"  # Eliminar duplicados
        }

        async with aiohttp.ClientSession() as session:
            try:
                print(f"📡 Haciendo petición a Google API...")
                async with session.get(url, params=params, timeout=15) as response:
                    response_text = await response.text()
                    print(f"📊 Status: {response.status}")

                    if response.status == 403:
                        print(f"❌ Error 403: {response_text[:500]}")
                        raise ValueError(
                            "❌ API Key inválida o límite de cuota excedido. Verifica: https://console.cloud.google.com/apis/dashboard")
                    elif response.status == 400:
                        print(f"❌ Error 400: {response_text[:500]}")
                        raise ValueError(" Bro respeta... (codigo de error 2)")
                    elif response.status != 200:
                        print(f"❌ Error {response.status}: {response_text[:500]}")
                        raise ValueError(f"❌ Error de API: {response.status}")

                    data = await response.json()
                    print(f"✅ Respuesta recibida: {len(data.get('items', []))} resultados")

            except asyncio.TimeoutError:
                print("❌ Timeout al conectar")
                raise ValueError("❌ El servidor de Google tardó mucho en responder. Intenta de nuevo.")
            except aiohttp.ClientError as e:
                print(f"❌ Error de conexión: {e}")
                raise ValueError(f"❌ Error de conexión: {str(e)}")
            except ValueError as e:
                # Pasar los ValueErrors directamente (ej. el 400 o 403)
                raise e
            except Exception as e:
                print(f"❌ Error inesperado: {e}")
                raise ValueError(f"❌ Error inesperado: {str(e)}")

        items = data.get("items", [])
        if not items:
            print("⚠️ No se encontraron resultados")
            return []

        results = []
        for item in items:
            try:
                image_url = item.get("link")
                thumbnail = item.get("image", {}).get("thumbnailLink", image_url)
                title = item.get("title", "Sin título")
                context = item.get("image", {}).get("contextLink", "")

                if image_url:
                    results.append(ImageResult(image_url, thumbnail, title, context))
            except Exception as e:
                print(f"⚠️ Error procesando resultado: {e}")
                continue

        print(f"✅ Procesados {len(results)} resultados válidos")
        return results

    async def execute_search(self, query: str, context) -> None:
        """
        Ejecuta la búsqueda y muestra los resultados.

        Args:
            query: Término de búsqueda
            context: Contexto del comando (ctx o interaction)
        """
        # Determinar si es comando tradicional o slash command
        is_interaction = isinstance(context, discord.Interaction)

        if is_interaction:
            await context.response.defer()

        # Validar query
        if not query or len(query.strip()) == 0:
            error_msg = "❌ Debes especificar qué buscar.\n**Ejemplo:** `¿img gatos programando`"
            if is_interaction:
                await context.followup.send(error_msg)
            else:
                await context.reply(error_msg)
            return

        query = query.strip()

        # Mensaje de carga
        if is_interaction:
            loading = await context.followup.send(f"🔍 Buscando imágenes de **{query}**...")
        else:
            loading = await context.reply(f"🔍 Buscando imágenes de **{query}**...")

        # Realizar búsqueda
        try:
            images = await self.search_images(query)
        except ValueError as e:
            error_embed = discord.Embed(
                title="❌ Error en la búsqueda",
                description=str(e),
                color=discord.Color.red()
            )

            if str(e).startswith("Faltan credenciales"):
                error_embed.add_field(
                    name="📋 Configuración requerida",
                    value=(
                        "1. Ve a: https://console.cloud.google.com/apis/credentials\n"
                        "2. Crea una API Key\n"
                        "3. Habilita 'Custom Search API'\n"
                        "4. Ve a: https://programmablesearchengine.google.com/\n"
                        "5. Crea un Search Engine y obtén el CX ID\n"
                        "6. Configura las variables de entorno:\n"
                        "   - `GOOGLE_SEARCH_API_KEY`\n"
                        "   - `GOOGLE_SEARCH_CX_ID`"
                    ),
                    inline=False
                )

            await loading.edit(content="", embed=error_embed)
            return
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Error inesperado",
                description=f"Ocurrió un error: {str(e)}",
                color=discord.Color.red()
            )
            await loading.edit(content="", embed=error_embed)
            return

        # Verificar resultados
        if not images:
            no_results = discord.Embed(
                title="🔍 Sin resultados",
                description=f"No encontré imágenes para: **{query}**\n\nIntenta con otros términos de búsqueda.",
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
    print("✅ ImageSearchCog (commands.image) cargado correctamente.")