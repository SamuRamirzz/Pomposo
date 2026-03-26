import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv
import pytz
from fuzzywuzzy import fuzz

# Cargar entorno
load_dotenv()

# Configuración de Archivo
AGENDA_FILE = 'agenda_data.json'

# Configuración de Gemini
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f" Error al inicializar Gemini en agenda: {e}")
else:
    print(" GEMINI_API_KEY no encontrada en agenda.py")

class AgendaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = self.load_data()
        # Iniciar loop de recordatorios
        self.check_reminders.start()

    def cog_unload(self):
        self.check_reminders.cancel()

    def load_data(self):
        """Carga los datos de la agenda desde el JSON."""
        if not os.path.exists(AGENDA_FILE):
            return {}
        try:
            with open(AGENDA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f" Error cargando agenda: {e}")
            return {}

    def save_data(self):
        """Guarda los datos actuales en el JSON."""
        try:
            with open(AGENDA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f" Error guardando agenda: {e}")

    def get_user_data(self, user_id):
        """Obtiene o inicializa los datos de un usuario."""
        uid = str(user_id)
        if uid not in self.data:
            self.data[uid] = {"tasks": [], "reminders": []}
        return self.data[uid]

    def get_current_time(self):
        """Obtiene la hora actual en Colombia (zona horaria del bot)."""
        tz = pytz.timezone('America/Bogota')
        return datetime.datetime.now(tz)

    def cleanup_old_completed_tasks(self, user_data):
        """Elimina tareas completadas que tengan más de 24 horas."""
        tasks_list = user_data["tasks"]
        now = datetime.datetime.now()
        to_remove = []
        
        for i, task in enumerate(tasks_list):
            if task["status"] == "done":
                try:
                    created_at = datetime.datetime.fromisoformat(task["created_at"])
                    # Si la tarea fue completada hace más de 24 horas
                    if (now - created_at).total_seconds() > 86400:  # 24 horas en segundos
                        to_remove.append(i)
                except:
                    continue
        
        # Eliminar en orden inverso
        for index in sorted(to_remove, reverse=True):
            tasks_list.pop(index)
        
        return len(to_remove) > 0

    def reindex_tasks(self, tasks_list):
        """Reindexar los IDs de las tareas para que sean consecutivos."""
        for i, task in enumerate(tasks_list, start=1):
            task["id"] = i

    # --- Comandos de Tareas (To-Do) ---

    @commands.group(name="agenda", invoke_without_command=True)
    async def agenda(self, ctx):
        """Muestra tu agenda (tareas y recordatorios)."""
        # Limpiar tareas completadas antiguas antes de mostrar
        user_data = self.get_user_data(ctx.author.id)
        if self.cleanup_old_completed_tasks(user_data):
            self.save_data()
        await self.list_tasks(ctx)

    @agenda.command(name="add")
    async def add_task(self, ctx, *, task_text: str = None):
        """Agrega una tarea a tu lista. Uso: ¿agenda add Comprar pan"""
        if not task_text:
            msg = await ctx.send("» Uso: `¿agenda add <tarea>`\n» Ejemplo: `¿agenda add Comprar pan`")
            await msg.delete(delay=18)
            return
            
        user_data = self.get_user_data(ctx.author.id)
        
        # Generar ID simple (max id + 1)
        tasks_list = user_data["tasks"]
        new_id = 1
        if tasks_list:
            new_id = max(t["id"] for t in tasks_list) + 1
            
        new_task = {
            "id": new_id,
            "text": task_text,
            "status": "pending",
            "created_at": datetime.datetime.now().isoformat()
        }
        
        tasks_list.append(new_task)
        self.save_data()
        
        msg = await ctx.send(f" Tarea agregada: **{task_text}** (ID: {new_id})")
        await msg.delete(delay=18)

    @agenda.command(name="list")
    async def list_tasks(self, ctx):
        """Lista todas tus tareas y recordatorios pendientes."""
        user_data = self.get_user_data(ctx.author.id)
        tasks_list = user_data["tasks"]
        reminders_list = user_data.get("reminders", [])
        
        if not tasks_list and not reminders_list:
            await ctx.send(" No tienes tareas ni recordatorios pendientes. ¡Eres libre!")
            return

        embed = discord.Embed(
            title=f" Agenda de {ctx.author.display_name}",
            color=discord.Color.blue()
        )
        
        # Tareas
        pending_text = ""
        done_text = ""
        for t in tasks_list:
            status_icon = "" if t["status"] == "pending" else ""
            line = f"`#{t['id']}` {status_icon} {t['text']}\n"
            if t["status"] == "pending":
                pending_text += line
            else:
                done_text += line
                
        if pending_text:
            embed.add_field(name=" Tareas Por Hacer", value=pending_text, inline=False)
        
        # Recordatorios
        reminders_text = ""
        for r in reminders_list:
            # Formatear fecha para mostrar
            try:
                dt = datetime.datetime.fromisoformat(r['timestamp'])
                time_str = dt.strftime("%d/%m %I:%M %p")
                reminders_text += f" **{time_str}**: {r['reason']}\n"
            except:
                continue
        
        if reminders_text:
            embed.add_field(name=" Próximos Recordatorios", value=reminders_text, inline=False)

        if done_text:
            embed.add_field(name=" Completadas", value=done_text, inline=False)
            
        embed.set_footer(text="Comandos: ¿agenda add/check/del/recordar | ¿agenda para ver todo")
        await ctx.send(embed=embed)

    @agenda.command(name="check")
    async def check_task(self, ctx, *, query: str = None):
        """Marca una tarea como completada. Uso: ¿agenda check 1 o ¿agenda check comprar pan"""
        if not query:
            msg = await ctx.send(" **Error de sintaxis**\n» Uso: `¿agenda check <id o texto>`\n» Ejemplo: `¿agenda check 1`")
            await msg.delete(delay=18)
            return
            
        user_data = self.get_user_data(ctx.author.id)
        tasks_list = user_data["tasks"]
        
        # Intentar primero como ID
        try:
            task_id = int(query)
            for t in tasks_list:
                if t["id"] == task_id:
                    t["status"] = "done" if t["status"] == "pending" else "pending"
                    self.save_data()
                    status_msg = "completada" if t["status"] == "done" else "pendiente"
                    msg = await ctx.send(f" Tarea #{task_id} marcada como **{status_msg}**.")
                    await msg.delete(delay=18)
                    return
        except ValueError:
            # Si no es un número, usar fuzzy matching
            best_match = None
            best_score = 0
            query_lower = query.lower()
            
            for t in tasks_list:
                score = fuzz.partial_ratio(query_lower, t["text"].lower())
                if score > best_score:
                    best_score = score
                    best_match = t
            
            if best_score >= 65 and best_match:
                best_match["status"] = "done" if best_match["status"] == "pending" else "pending"
                self.save_data()
                status_msg = "completada" if best_match["status"] == "done" else "pendiente"
                msg = await ctx.send(f" Tarea #{best_match['id']} marcada como **{status_msg}**: {best_match['text']}")
                await msg.delete(delay=18)
                return
                
        msg = await ctx.send(f" No encontré ninguna tarea que coincida con '{query}'.")
        await msg.delete(delay=18)

    @agenda.command(name="del")
    async def delete_task(self, ctx, *, query: str = None):
        """Elimina una tarea permanentemente. Uso: ¿agenda del 1 o ¿agenda del comprar pan"""
        if not query:
            msg = await ctx.send(" **Error de sintaxis**\n» Uso: `¿agenda del <id o texto>`\n» Ejemplo: `¿agenda del 1`")
            await msg.delete(delay=18)
            return
            
        user_data = self.get_user_data(ctx.author.id)
        tasks_list = user_data["tasks"]
        
        # Intentar primero como ID
        try:
            task_id = int(query)
            for i, t in enumerate(tasks_list):
                if t["id"] == task_id:
                    removed = tasks_list.pop(i)
                    # Reindexar IDs después de eliminar
                    self.reindex_tasks(tasks_list)
                    self.save_data()
                    msg = await ctx.send(f" Tarea eliminada: **{removed['text']}**")
                    await msg.delete(delay=18)
                    return
        except ValueError:
            # Si no es un número, usar fuzzy matching
            best_match = None
            best_match_index = -1
            best_score = 0
            query_lower = query.lower()
            
            for i, t in enumerate(tasks_list):
                score = fuzz.partial_ratio(query_lower, t["text"].lower())
                if score > best_score:
                    best_score = score
                    best_match = t
                    best_match_index = i
            
            if best_score >= 65 and best_match:
                removed = tasks_list.pop(best_match_index)
                # Reindexar IDs después de eliminar
                self.reindex_tasks(tasks_list)
                self.save_data()
                msg = await ctx.send(f" Tarea eliminada: **{removed['text']}**")
                await msg.delete(delay=18)
                return
                
        msg = await ctx.send(f" No encontré ninguna tarea que coincida con '{query}'.")
        await msg.delete(delay=18)

    # --- Comandos de Recordatorios (Híbrido: Dateparser + AI) ---
    @agenda.command(name="recordar")
    async def remind(self, ctx, *, query: str = None):
        """
        Crea un recordatorio inteligente.
        Uso: ¿agenda recordar en 10 minutos sacar la pizza
        """
        if not query:
            msg = await ctx.send(" **Error de sintaxis**\n» Uso: `¿agenda recordar <cuando> <qué>`\n» Ejemplo: `¿agenda recordar en 10 minutos sacar la pizza`")
            await msg.delete(delay=15)
            return
            
        # Intentar usar dateparser primero (Más rápido y determinista)
        try:
            from dateparser.search import search_dates
            
            # Configuración para preferir fechas futuras y usar español
            settings = {
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': datetime.datetime.now()
            }
            
            # Buscar fechas en el texto
            dates = search_dates(query, languages=['es'], settings=settings)
            
            if dates:
                # Tomar la última fecha encontrada (suele ser la más relevante si hay varias)
                matched_text, date_obj = dates[-1]
                
                # La razón es el texto original menos la parte de la fecha
                reason = query.replace(matched_text, "").strip()
                
                # Limpieza básica de conectores que pueden quedar colgando
                for connector in [" en ", " el ", " para ", " a las "]:
                    if reason.endswith(connector.strip()):
                        reason = reason[:-len(connector.strip())].strip()
                
                # Si no queda razón, usar un default
                if not reason:
                    reason = "Recordatorio"
                
                # Guardar
                await self.save_reminder(ctx, reason, date_obj)
                return
                
        except ImportError:
            print(" dateparser no instalado, usando solo AI.")
        except Exception as e:
            print(f" Error dateparser: {e}")

        # Fallback a AI si dateparser falla o no encuentra fecha
        if not client:
            await ctx.send(" No pude detectar la fecha y la IA no está configurada.")
            return

        async with ctx.typing():
            now = self.get_current_time()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
            
            prompt = f"""
            Actúa como un asistente de agenda.
            La fecha y hora actual es: {now_str}
            
            Analiza la siguiente petición del usuario y extrae:
            1. La razón del recordatorio (reason).
            2. La fecha y hora exacta para el recordatorio en formato ISO 8601 (timestamp).
            
            Petición: "{query}"
            
            Responde SOLO con un objeto JSON válido con las claves "reason" y "timestamp".
            Si no puedes determinar una fecha, usa null en "timestamp".
            """
            
            try:
                response = client.models.generate_content(
                    model='gemini-3.1-flash-lite-preview',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json'
                    )
                )
                
                result = json.loads(response.text)
                reason = result.get("reason")
                timestamp_str = result.get("timestamp")
                
                if not timestamp_str:
                    await ctx.send(" No pude entender cuándo quieres el recordatorio. Intenta ser más específico con la hora.")
                    return
                
                date_obj = datetime.datetime.fromisoformat(timestamp_str)
                await self.save_reminder(ctx, reason, date_obj)
                
            except Exception as e:
                print(f"Error en remind AI: {e}")
                await ctx.send(" Ocurrió un error al procesar tu recordatorio.")

    async def save_reminder(self, ctx, reason, date_obj):
        """Helper para guardar el recordatorio y confirmar al usuario."""
        user_data = self.get_user_data(ctx.author.id)
        
        # Asegurar timezone aware
        if date_obj.tzinfo is None:
             # Asumir que dateparser devolvió hora local del sistema, asignar la del bot
             tz = pytz.timezone('America/Bogota')
             date_obj = date_obj.replace(tzinfo=tz)

        new_reminder = {
            "reason": reason,
            "timestamp": date_obj.isoformat(),
            "channel_id": ctx.channel.id,
            "created_at": datetime.datetime.now().isoformat()
        }
        
        user_data["reminders"].append(new_reminder)
        self.save_data()
        
        # Confirmación amigable
        nice_date = date_obj.strftime("%d/%m a las %I:%M %p")
        await ctx.send(f" ¡Entendido! Te recordaré: **{reason}** el **{nice_date}**.")

    @tasks.loop(minutes=1)
    async def check_reminders(self):
        """Revisa cada minuto si hay recordatorios pendientes."""
        now = datetime.datetime.now().astimezone() # Aware datetime
        changes = False
        
        for user_id, data in self.data.items():
            reminders = data.get("reminders", [])
            to_remove = []
            
            for i, r in enumerate(reminders):
                try:
                    rem_time = datetime.datetime.fromisoformat(r["timestamp"])
                    # Asegurar que rem_time tenga zona horaria si no la tiene (asumir local/colombia)
                    if rem_time.tzinfo is None:
                         rem_time = rem_time.replace(tzinfo=now.tzinfo)
                    
                    if now >= rem_time:
                        # ¡Es hora! Enviar DM al usuario
                        user = self.bot.get_user(int(user_id))
                        if user:
                            try:
                                embed = discord.Embed(
                                    title=" ¡Recordatorio!",
                                    description=f"**{r['reason']}**",
                                    color=discord.Color.gold()
                                )
                                embed.set_footer(text="Este recordatorio ha sido completado.")
                                await user.send(embed=embed)
                            except discord.Forbidden:
                                # Si no se puede enviar DM, intentar en el canal original
                                channel = self.bot.get_channel(r["channel_id"])
                                if channel:
                                    embed = discord.Embed(
                                        title=" ¡Recordatorio!",
                                        description=f"**{r['reason']}**",
                                        color=discord.Color.gold()
                                    )
                                    embed.set_footer(text="(No pude enviarte DM, así que te aviso aquí)")
                                    await channel.send(content=user.mention, embed=embed)
                        
                        to_remove.append(i)
                except Exception as e:
                    print(f"Error procesando recordatorio: {e}")
            
            # Eliminar recordatorios procesados (en orden inverso para no afectar índices)
            if to_remove:
                for index in sorted(to_remove, reverse=True):
                    reminders.pop(index)
                changes = True
        
        if changes:
            self.save_data()

    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()

    # === SLASH COMMANDS (AGENDA GROUP) ===
    agenda_group = app_commands.Group(name="agenda", description="Gestión de tareas y recordatorios")

    @agenda_group.command(name="add", description="Agrega una tarea")
    async def agenda_add_slash(self, interaction: discord.Interaction, tarea: str):
        await self.add_task(await self.bot.get_context(interaction), task_text=tarea)

    @agenda_group.command(name="list", description="Lista tus tareas")
    async def agenda_list_slash(self, interaction: discord.Interaction):
        await self.list_tasks(await self.bot.get_context(interaction))

    @agenda_group.command(name="check", description="Marca una tarea como completada")
    async def agenda_check_slash(self, interaction: discord.Interaction, id_o_texto: str):
        await self.check_task(await self.bot.get_context(interaction), query=id_o_texto)

    @agenda_group.command(name="del", description="Elimina una tarea")
    async def agenda_del_slash(self, interaction: discord.Interaction, id_o_texto: str):
        await self.delete_task(await self.bot.get_context(interaction), query=id_o_texto)

    @agenda_group.command(name="recordar", description="Crea un recordatorio")
    async def agenda_recordar_slash(self, interaction: discord.Interaction, cuando_y_que: str):
        await self.remind(await self.bot.get_context(interaction), query=cuando_y_que)


async def setup(bot):
    await bot.add_cog(AgendaCog(bot))
    print(" Module commands.agenda loaded")
