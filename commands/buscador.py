import discord
from discord.ext import commands
from discord import app_commands
import io
import datetime

class BuscadorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="buscar", description="Busca mensajes con una palabra clave en un servidor")
    async def buscar(self, interaction: discord.Interaction):
        # Crear la vista con el selector de servidores
        view = GuildSelectView(self.bot)
        if not view.options:
             await interaction.response.send_message("❌ No estoy en ningún servidor (o algo salió mal).", ephemeral=True)
             return
             
        await interaction.response.send_message("🌍 Selecciona un servidor para buscar:", view=view, ephemeral=True)

class GuildSelectView(discord.ui.View):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.options = []
        
        # Listar servidores donde está el bot
        for guild in bot.guilds:
            self.options.append(discord.SelectOption(label=guild.name[:100], value=str(guild.id)))
            
        # Limitar a 25 opciones (límite de Discord)
        selected_options = self.options[:25]

        if selected_options:
            select = discord.ui.Select(placeholder="Elige un servidor...", options=selected_options)
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        guild_id = int(interaction.data['values'][0])
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            await interaction.response.send_message("❌ No pude encontrar ese servidor.", ephemeral=True)
            return
            
        # Abrir el modal para pedir la palabra clave
        await interaction.response.send_modal(KeywordModal(guild))

class KeywordModal(discord.ui.Modal, title="Búsqueda de Mensajes"):
    keyword = discord.ui.TextInput(
        label="Palabra clave", 
        placeholder="Ej: presupuesto", 
        min_length=2, 
        max_length=50
    )
    
    date_input = discord.ui.TextInput(
        label="Fecha (Opcional)",
        placeholder="DD/MM/AAAA (Ej: 25/12/2025)",
        required=False,
        min_length=8,
        max_length=10
    )

    def __init__(self, guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        keyword_lower = self.keyword.value.lower()
        date_str = self.date_input.value.strip()
        found_messages = []
        
        # Configuración de búsqueda por defecto
        search_after = None
        search_before = None
        limit = 200 # Límite por defecto si NO hay fecha (aumentado de 50 a 200)
        
        # Si hay fecha, configurar rangos del día completo
        if date_str:
            try:
                # Parsear fecha (DD/MM/YYYY)
                day, month, year = map(int, date_str.split('/'))
                target_date = datetime.datetime(year, month, day)
                
                # Definir inicio y fin del día
                search_after = target_date
                search_before = target_date + datetime.timedelta(days=1)
                limit = None # Sin límite de cantidad si es por fecha
                
            except ValueError:
                await interaction.followup.send("❌ La fecha no tiene el formato correcto (DD/MM/AAAA).", ephemeral=True)
                return

        MAX_RESULTS = 25
        
        # Iterar sobre los canales de texto del servidor
        channel_count = 0
        channels_searched = 0
        
        for channel in self.guild.text_channels:
            # Verificar permisos de lectura
            permissions = channel.permissions_for(self.guild.me)
            if not permissions.read_messages or not permissions.read_message_history:
                continue
                
            channels_searched += 1
            try:
                # Búsqueda condicional
                if search_after:
                    iterator = channel.history(limit=None, after=search_after, before=search_before)
                else:
                    iterator = channel.history(limit=limit)
                    
                async for message in iterator:
                    if message.content and keyword_lower in message.content.lower() and not message.author.bot:
                        found_messages.append(message)
                        
            except Exception as e:
                # Ignorar errores de acceso (stealth)
                continue

        if not found_messages:
            scope_msg = f"del día {date_str}" if date_str else f"recientes ({limit} por canal)"
            await interaction.followup.send(
                f"❌ No encontré mensajes con **'{self.keyword.value}'** {scope_msg} en **{self.guild.name}**.",
                ephemeral=True
            )
            return

        # Ordenar por fecha (más reciente primero)
        found_messages.sort(key=lambda m: m.created_at, reverse=True)
        
        # Si hay demasiados, cortar
        display_messages = found_messages[:MAX_RESULTS]
        
        view = MessageSelectView(display_messages)
        await interaction.followup.send(
            f"✅ Encontré {len(found_messages)} coincidencia(s). Selecciona un mensaje para ver el contexto:",
            view=view,
            ephemeral=True
        )

class MessageSelectView(discord.ui.View):
    def __init__(self, messages):
        super().__init__()
        options = []
        for msg in messages:
            # Crear una etiqueta descriptiva: "Autor: Contenido..."
            label = f"{msg.author.name}: {msg.content}"
            # Truncar label si es muy largo (max 100)
            if len(label) > 95:
                label = label[:92] + "..."
            
            # Descripción: Canal y Fecha
            date_str = msg.created_at.strftime("%d/%m %H:%M")
            description = f"#{msg.channel.name} - {date_str}"
            
            options.append(discord.SelectOption(
                label=label, 
                value=str(msg.id), 
                description=description
            ))
            
        select = discord.ui.Select(placeholder="Selecciona un mensaje...", options=options)
        select.callback = self.select_callback
        self.add_item(select)
        
        # Guardar referencia a los mensajes para usarlos en el callback
        self.messages_map = {str(m.id): m for m in messages}

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        message_id = interaction.data['values'][0]
        original_msg = self.messages_map.get(message_id)
        
        if not original_msg:
             # Fallback por si la referencia se perdió (raro en esta ejecución)
             await interaction.followup.send("❌ Error: No se pudo recuperar el mensaje original.", ephemeral=True)
             return

        # Obtener contexto (15 antes y 15 después -> aprox 31 total centrado)
        try:
            # around busca mensajes ALREDEDOR del ID, sin importar la fecha
            context_msgs = [msg async for msg in original_msg.channel.history(around=original_msg, limit=31)]
            context_msgs.sort(key=lambda m: m.created_at)
            
            # Construir el transcript
            output_lines = [f"--- Contexto en #{original_msg.channel.name} ({original_msg.guild.name}) ---", ""]
            
            for m in context_msgs:
                timestamp = m.created_at.strftime("%d/%m %H:%M:%S")
                base_line = f"[{timestamp}] {m.author.name}: {m.content}"
                
                # Resaltar el mensaje seleccionado
                if m.id == original_msg.id:
                    output_lines.append(f"👉 {base_line}  <-- SELECCIONADO")
                else:
                    output_lines.append(f"   {base_line}")
            
            full_text = "\n".join(output_lines)
            
            # Si cabe en un mensaje de Discord (con código)
            if len(full_text) < 1900:
                await interaction.followup.send(f"```text\n{full_text}\n```", ephemeral=True)
            else:
                # Si es muy largo, enviar archivo
                f = io.BytesIO(full_text.encode('utf-8'))
                await interaction.followup.send(
                    "📄 El contexto es extenso, aquí tienes el archivo:",
                    file=discord.File(f, filename="contexto_mensaje.txt"),
                    ephemeral=True
                )
                
        except Exception as e:
            await interaction.followup.send(f"❌ Error al obtener contexto: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(BuscadorCog(bot))
    print("✅ Module commands.buscador loaded")
