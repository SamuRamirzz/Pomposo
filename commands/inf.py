import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import random

# ---  CONFIGURACIÓN ---
BOT_VERSION = "1.3.0v"
BOT_NAME = "Pomposo"
BOT_CREATOR = "Drake (drake_dev#0)"
BOT_DESCRIPTION = "¡Ola! Soi Pomposo, una IA normalita pero con acceso a todo el internet, ¡jeje! Así que si me preguntas algo, te lo busco y te lo digo, pemdejo."
CAT_API_URL = "https://api.thecatapi.com/v1/images/search"


class InfoMenu(discord.ui.Select):
    """Menú desplegable para navegar entre secciones."""

    def __init__(self, cog):
        self.cog = cog

        options = [
            discord.SelectOption(
                label=" Información General",
                description="Información sobre el bot",
                emoji="",
                value="info",
                default=True
            ),
            discord.SelectOption(
                label=" Ayuda - Comandos",
                description="Lista de comandos disponibles",
                emoji="",
                value="help"
            ),
            discord.SelectOption(
                label=" Errores Comunes",
                description="Soluciones a problemas frecuentes",
                emoji="",
                value="errors"
            ),
            discord.SelectOption(
                label=" Estadísticas",
                description="Estadísticas del bot",
                emoji="",
                value="stats"
            ),
            discord.SelectOption(
                label=" Enlaces Útiles",
                description="Links importantes y recursos",
                emoji="",
                value="links"
            ),
            discord.SelectOption(
                label=" Changelog",
                description="Cambios de la última versión",
                emoji="",
                value="changelog"
            )
        ]

        super().__init__(
            placeholder=" Selecciona una sección...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        """Maneja la selección del menú."""
        selected = self.values[0]

        # Actualizar opciones visuales
        for option in self.options:
            option.default = (option.value == selected)

        # Obtener nuevo embed según la selección
        if selected == "info":
            embed = await self.cog.build_info_embed()
        elif selected == "help":
            embed = self.cog.build_help_embed()
        elif selected == "errors":
            embed = self.cog.build_errors_embed()
        elif selected == "stats":
            embed = await self.cog.build_stats_embed()
        elif selected == "links":
            embed = self.cog.build_links_embed()
        elif selected == "changelog":
            embed = self.cog.build_changelog_embed()
        else:
            embed = await self.cog.build_info_embed()

        await interaction.response.edit_message(embed=embed, view=self.view)


class InfoView(discord.ui.View):
    """Vista con el menú desplegable."""

    def __init__(self, cog):
        super().__init__(timeout=300)  # 5 minutos
        self.add_item(InfoMenu(cog))


class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_random_cat(self) -> str:
        """Obtiene una imagen aleatoria de gato desde TheCatAPI."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(CAT_API_URL, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and len(data) > 0:
                            return data[0]['url']
        except Exception as e:
            print(f" Error obteniendo gato: {e}")

        # Fallback: lista de gatos de ejemplo
        fallback_cats = [
            "https://cataas.com/cat",
            "https://cataas.com/cat/cute",
            "https://cataas.com/cat/says/meow"
        ]
        return random.choice(fallback_cats)

    async def build_info_embed(self) -> discord.Embed:
        """Construye el embed de información general."""
        embed = discord.Embed(
            title=f" {BOT_NAME}",
            description=BOT_DESCRIPTION,
            color=discord.Color.blue()
        )

        # Información del bot
        embed.add_field(
            name=" Creador",
            value=BOT_CREATOR,
            inline=True
        )

        embed.add_field(
            name=" Versión",
            value=f"`{BOT_VERSION}`",
            inline=True
        )

        embed.add_field(
            name=" Servidores",
            value=f"`{len(self.bot.guilds)}`",
            inline=True
        )

        # Descripción resumida de comandos
        embed.add_field(
            name=" Tipos de Comandos",
            value=(
                " **Inteligencia Artificial** - Conversación y respuestas\n"
                " **Búsqueda** - Imágenes y videos\n"
                " **Moderación** - Gestión de usuarios\n"
                " **Diversión** - Interacciones divertidas\n"
                " **Utilidades** - Herramientas varias\n\n"
                " Usa el menú para ver la lista completa de comandos"
            ),
            inline=False
        )

        # Imagen de gato
        cat_url = await self.get_random_cat()
        embed.set_thumbnail(url=cat_url)

        embed.set_footer(
            text="Usa el menú desplegable para ver más información",
            icon_url=self.bot.user.display_avatar.url
        )

        return embed

    def build_help_embed(self) -> discord.Embed:
        """Construye el embed de ayuda con comandos."""
        embed = discord.Embed(
            title=" Ayuda - Comandos Disponibles",
            description="Lista de todos los comandos del bot y su funcionalidad.",
            color=discord.Color.green()
        )

        # Comandos principales
        commands_list = [
            (" `¿ask <pregunta>`",
             "Conversa con la IA. Puede responder preguntas, escribir código, crear historias y más."),
            (" `¿img <búsqueda>`", "Busca imágenes en Google. Navega entre 25 resultados con botones."),
            (" `¿yt <búsqueda>`", "Busca videos en YouTube. Navega entre 20 resultados con información detallada."),
            (" `¿deal <juego>`", "Busca las mejores ofertas de un juego en tiendas online."),
            (" `¿agenda`", "Gestiona tareas y recordatorios inteligentes con IA."),
            (" `¿tts <personaje> <texto>`", "Genera audio TTS con voces de personajes usando FakeYou."),
            (" `¿nick <usuario> <apodo>`", "Cambia el apodo de un usuario en el servidor."),
            (" `¿punch <usuario>`", "Dale un puñetazo a alguien con un GIF de gatos peleando."),
            (" `¿server`", "Panel de control para el servidor de Minecraft en Google Cloud."),
            (" `¿info`", "Muestra este panel de información y ayuda.")
        ]

        for cmd, desc in commands_list:
            embed.add_field(
                name=cmd,
                value=desc,
                inline=False
            )

        # Comandos del Arquitecto (solo dueño)
        embed.add_field(
            name=" Comandos del Arquitecto (Solo Dueño)",
            value=(
                "`¿create <instrucción>` - Crea o edita código con IA\n"
                "`¿ok` / `¿si` - Instala el código generado\n"
                "`¿ver` - Mira el código que vas a instalar\n"
                "`¿no` - Descarta todo y empieza de nuevo\n"
                "`¿parches` - Lista errores pendientes\n"
                "`¿fix <id>` - Aplica un parche específico\n"
                "`¿undo <archivo>` - Restaura desde backup\n"
                "`¿reiniciar` - Reinicia el bot si explota"
            ),
            inline=False
        )

        # Comandos adicionales
        embed.add_field(
            name=" Tip",
            value="Todos los comandos también funcionan con `/` (slash commands). Ejemplo: `/img gatos`",
            inline=False
        )

        embed.set_footer(
            text=f"{BOT_NAME} v{BOT_VERSION}",
            icon_url=self.bot.user.display_avatar.url
        )

        return embed

    def build_errors_embed(self) -> discord.Embed:
        """Construye el embed de errores comunes."""
        embed = discord.Embed(
            title=" Errores Comunes y Soluciones",
            description="Soluciones a los problemas más frecuentes del bot.",
            color=discord.Color.orange()
        )

        # Error 1: Cuota API excedida
        embed.add_field(
            name=" Error #1: Cuota de API Excedida",
            value=(
                "**Mensaje:** `Se me acabaron los créditos de búsqueda...` o `API Key inválida o límite de cuota excedido`\n\n"
                "**Causa:** El bot alcanzó el límite diario de búsquedas en Google/YouTube.\n\n"
                "**Solución:**\n"
                "• Espera 24 horas para que se restablezca la cuota\n"
                "• Los comandos `¿ask` y `¿dl` siguen funcionando normalmente\n"
                "• Contacta al administrador del bot si es urgente"
            ),
            inline=False
        )

        # Error 2: Custom Search API no habilitada
        embed.add_field(
            name="🟠 Error #2: Custom Search API no Habilitada",
            value=(
                "**Mensaje:** `Búsqueda inválida o Custom Search API no habilitada`\n\n"
                "**Causa:** La API de búsqueda de Google no está configurada correctamente.\n\n"
                "**Solución (Solo Administradores):**\n"
                "1. Ve a: https://console.cloud.google.com/apis/library\n"
                "2. Busca 'Custom Search API' y habilítala\n"
                "3. Verifica que la API Key tenga permisos correctos\n"
                "4. Reinicia el bot"
            ),
            inline=False
        )

        embed.set_footer(
            text="Si el error persiste, contacta al creador del bot",
            icon_url=self.bot.user.display_avatar.url
        )

        return embed

    async def build_stats_embed(self) -> discord.Embed:
        """Construye el embed de estadísticas."""
        embed = discord.Embed(
            title=" Estadísticas del Bot",
            description="Información sobre el uso y rendimiento del bot.",
            color=discord.Color.purple()
        )

        # Estadísticas básicas
        total_members = sum(guild.member_count for guild in self.bot.guilds)

        embed.add_field(
            name=" Servidores",
            value=f"`{len(self.bot.guilds)}`",
            inline=True
        )

        embed.add_field(
            name=" Usuarios Totales",
            value=f"`{total_members:,}`",
            inline=True
        )

        embed.add_field(
            name=" Canales",
            value=f"`{sum(len(guild.channels) for guild in self.bot.guilds)}`",
            inline=True
        )

        # Comandos cargados
        total_commands = len([cmd for cmd in self.bot.walk_commands()])
        slash_commands = len(self.bot.tree.get_commands())

        embed.add_field(
            name=" Comandos Tradicionales",
            value=f"`{total_commands}`",
            inline=True
        )

        embed.add_field(
            name=" Slash Commands",
            value=f"`{slash_commands}`",
            inline=True
        )

        embed.add_field(
            name=" Extensiones",
            value=f"`{len(self.bot.extensions)}`",
            inline=True
        )

        # Latencia
        latency_ms = round(self.bot.latency * 1000)
        latency_emoji = "🟢" if latency_ms < 100 else "🟡" if latency_ms < 200 else ""

        embed.add_field(
            name=" Latencia",
            value=f"{latency_emoji} `{latency_ms} ms`",
            inline=True
        )

        # Imagen de gato
        cat_url = await self.get_random_cat()
        embed.set_thumbnail(url=cat_url)

        embed.set_footer(
            text=f"Bot activo desde la versión {BOT_VERSION}",
            icon_url=self.bot.user.display_avatar.url
        )

        return embed

    def build_links_embed(self) -> discord.Embed:
        """Construye el embed de enlaces útiles."""
        embed = discord.Embed(
            title=" Enlaces Útiles",
            description="Recursos y links importantes relacionados con el bot.",
            color=discord.Color.teal()
        )

        # Enlaces de configuración
        embed.add_field(
            name=" Configuración de APIs",
            value=(
                "• [Google Cloud Console](https://console.cloud.google.com)\n"
                "• [Custom Search Engine](https://programmablesearchengine.google.com)\n"
                "• [YouTube API](https://console.cloud.google.com/apis/library/youtube.googleapis.com)\n"
                "• [TheCat API](https://thecatapi.com)\n"
                "• [Tenor API](https://tenor.com/gifapi)"
            ),
            inline=False
        )

        # Enlaces de desarrollo
        embed.add_field(
            name=" Desarrollo",
            value=(
                "• [Discord.py Docs](https://discordpy.readthedocs.io)\n"
                "• [Discord Developer Portal](https://discord.com/developers/applications)\n"
                "• [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)"
            ),
            inline=False
        )

        # Enlaces de soporte
        embed.add_field(
            name=" Soporte y Comunidad",
            value=(
                    "• **Creador:** Drake (drake_dev#0)\n"
                    "• **Versión:** " + BOT_VERSION + "\n"
                                                      "• **Prefix:** `¿` o `/` (slash commands)"
            ),
            inline=False
        )

        # Tecnologías usadas
        embed.add_field(
            name=" Tecnologías",
            value=(
                "• **Discord.py** - Framework del bot\n"
                "• **Google Cloud** - APIs de búsqueda\n"
                "• **Tenor API** - GIFs animados\n"
                "• **FakeYou API** - TTS con voces de personajes\n"
                "• **yt-dlp** - Descarga de videos\n"
                "• **FFmpeg** - Procesamiento multimedia\n"
                "• **mcstatus** - Status de Minecraft"
            ),
            inline=False
        )

        embed.set_footer(
            text="Gracias por usar " + BOT_NAME,
            icon_url=self.bot.user.display_avatar.url
        )

        return embed

    def build_changelog_embed(self) -> discord.Embed:
        """Construye el embed del changelog."""
        embed = discord.Embed(
            title=" Changelog - Versión 1.3.0",
            description="¡Nueva actualización con varias mejoras y un nuevo comando!",
            color=discord.Color.gold()
        )

        # Nuevas funciones
        embed.add_field(
            name=" Nuevas Funciones",
            value=(
                "• **`¿tts <personaje> <texto>`** - ¡Nuevo comando TTS!\n"
                "  Genera audio con voces de personajes como Sans, Hornet, SpongeBob y más.\n"
                "  Usa fuzzy matching para encontrar voces similares.\n"
                "  Incluye autocomplete en el slash command `/tts`."
            ),
            inline=False
        )

        # Mejoras
        embed.add_field(
            name=" Mejoras",
            value=(
                "• **Búsqueda de imágenes**: Ahora muestra 25 imágenes (antes 10)\n"
                "• **Comando block**: Umbral de coincidencias ajustado para mayor precisión\n"
                "• **Agenda**: Mensajes temporales ahora duran 18 segundos (antes 10)\n"
                "• **Info**: Nueva sección de Changelog agregada"
            ),
            inline=False
        )

        # Correcciones
        embed.add_field(
            name=" Ajustes Técnicos",
            value=(
                "• Optimización del fuzzy matching en comandos de moderación\n"
                "• Mejor manejo de errores en comandos de búsqueda\n"
                "• Actualización de tecnologías en la sección de enlaces"
            ),
            inline=False
        )

        embed.set_footer(
            text=f"{BOT_NAME} v{BOT_VERSION} • Diciembre 2024",
            icon_url=self.bot.user.display_avatar.url
        )

        return embed

    async def execute_info(self, context):
        """Ejecuta el comando info."""
        is_interaction = isinstance(context, discord.Interaction)

        if is_interaction:
            await context.response.defer()

        # Construir embed inicial
        embed = await self.build_info_embed()

        # Crear vista con menú
        view = InfoView(self)

        # Enviar mensaje
        if is_interaction:
            await context.followup.send(embed=embed, view=view)
        else:
            await context.reply(embed=embed, view=view)

    # --- COMANDO TRADICIONAL ---
    @commands.command(name="info", aliases=["about"])
    async def info_command(self, ctx):
        """
        Muestra información sobre el bot.

        Uso: ¿info
        """
        await self.execute_info(ctx)

    @commands.command(name="help", aliases=["ayuda"])
    async def help_command(self, ctx):
        """
        Muestra la sección de ayuda directamente.

        Uso: ¿help
        """
        is_interaction = False

        # Construir embed de ayuda
        embed = self.build_help_embed()

        # Crear vista con menú (pero seleccionando "help" por defecto)
        view = InfoView(self)

        # Actualizar opciones del menú para que "help" esté seleccionado
        for item in view.children:
            if isinstance(item, InfoMenu):
                for option in item.options:
                    option.default = (option.value == "help")
                break

        await ctx.reply(embed=embed, view=view)

    # --- SLASH COMMAND ---
    @app_commands.command(name="info", description="Información sobre el bot")
    async def info_slash(self, interaction: discord.Interaction):
        """Muestra información usando slash command."""
        await self.execute_info(interaction)


async def setup(bot):
    await bot.add_cog(InfoCog(bot))
    print(" InfoCog (commands.info) cargado correctamente.")