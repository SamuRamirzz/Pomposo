"""
ARCHITECT COG V3 - El cerebro técnico de Pomposo 
Sistema inteligente de creación/edición/auto-reparación de código.

Comandos (solo dueño):
 Creación 
  ¿nuevo <instrucción>   → Crea un comando nuevo
  ¿editar <instrucción>  → Edita un archivo existente

 Staging 
  ¿ok / ¿si              → Confirma código en staging
  ¿ver                   → Muestra código pendiente
  ¿no                    → Descarta código

 Parches 
  ¿parches               → Lista errores y parches pendientes
  ¿fix [id]              → Aplica un parche específico
  ¿explica [id]          → Explica un error/parche

 Utilidades 
  ¿undo <archivo>        → Restaura desde backup
  ¿backups [archivo]     → Lista backups disponibles
  ¿historial             → Muestra historial de cambios
  ¿reiniciar             → Reinicia el bot
  ¿help arquitecto       → Muestra esta ayuda
"""

import os
import re
import json
import discord
import traceback
import random
import inspect
from datetime import datetime
from discord.ext import commands
from discord import app_commands
from google import genai
from pathlib import Path

# Importar el SafeEditor V2
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from safe_editor import safe_editor, ErrorClassifier, ErrorSeverity, COMMANDS_DIR, STAGING_DIR

# 
#  CONFIGURACIÓN
# 

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
architect_client = None
if GEMINI_API_KEY:
    try:
        architect_client = genai.Client(api_key=GEMINI_API_KEY)
        print(" Arquitecto V3 conectado a Gemini.")
    except Exception as e:
        print(f" Error al inicializar Arquitecto: {e}")

# Cargar Personalidad
PERSONALITY_FILE = Path(__file__).parent.parent / "ask_personalidad.txt"
def load_personality():
    try:
        if PERSONALITY_FILE.exists():
            with open(PERSONALITY_FILE, 'r', encoding='utf-8') as f:
                return f.read()
    except:
        pass
    return "Eres Pomposo, una IA sarcástica."

POMPOSO_PERSONALITY = load_personality()

# 
#  PROMPTS
# 

ARCHITECT_PROMPT = f"""
ROL: Eres el "Arquitecto", el lado técnico de Pomposo.
Escribes y modificas código Python para un bot de Discord (discord.py).

TU PERSONALIDAD:
{POMPOSO_PERSONALITY}

CONTEXTO TÉCNICO:
- Python asíncrono (async/await), Cogs (commands.Cog)
- Prefijo del bot: ¿
- Comentarios sarcásticos estilo Pomposo
- NUNCA elimines @commands.is_owner() de comandos protegidos
- Incluye SIEMPRE async def setup(bot) al final

INSTRUCCIONES:
1. Devuelve SOLO código Python válido en bloque ```python
2. Para comandos nuevos: estructura completa de Cog con setup()
3. Para ediciones: archivo completo modificado
4. Importa TODAS las librerías necesarias
"""

INTENT_PROMPT = """
Analiza esta solicitud y determina la intención:

SOLICITUD: "{instruction}"

Responde SOLO con un JSON:
{{
    "action": "create" o "edit",
    "target_file": "nombre_archivo.py" o null,
    "command_name": "nombre_comando" o null,
    "description": "breve descripción"
}}

Solo JSON válido, sin explicaciones ni markdown.
"""

DIAGNOSIS_PROMPT = """
{architect_prompt}

MODO: DIAGNÓSTICO Y REPARACIÓN

El comando '{command_name}' falló con este error:

TIPO DE ERROR: {error_type}
SEVERIDAD: {severity}

TRACEBACK:
```
{error_tb}
```

CÓDIGO ACTUAL ({file_name}):
```python
{source_code}
```

INSTRUCCIONES:
1. Analiza qué causó el error
2. Genera el código COMPLETO del archivo corregido en bloque ```python
3. Mantén toda la funcionalidad existente
4. Corrige SOLO lo necesario para resolver el error
"""

# Frases del Arquitecto
PHRASES = {
    "thinking": [
        " Déjame ver qué puedo hacer...",
        " Analizando... tantito...",
        " Va va, ahorita lo cocino...",
        " Procesando tu solicitud...",
    ],
    "success": [
        " Listo, usa `¿ok` si te late o `¿ver` para revisarlo",
        " Ahí ta, `¿ok` para aplicar",
        " Ya quedó, `¿ok` para instalar",
    ],
    "error": [
        " Algo salió mal: ",
        " Esto no da: ",
        " Error: ",
    ],
    "auto_fix": [
        " Se detectó un error reparable. Aplicando parche automáticamente...",
        " Error trivial detectado. Reparando sin preguntar...",
        " Auto-reparación activada...",
    ],
}


# 
#  ARCHITECT COG V3
# 

class ArchitectCog(commands.Cog):
    """
    Arquitecto V3 - Sistema inteligente de código con IA.
    Auto-reparación, creación, edición y gestión de parches.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.pending_file_name = None
        self.pending_action = None  # "create" o "edit"
        self.pending_patches = {}  # {id: {error, code, file, traceback, severity}}
        self.patch_counter = 0
        self.error_cooldown = {}  # {command_name: last_error_time} anti-spam
        self.auto_fix_count = 0   # Contador de auto-reparaciones exitosas
        
    # 
    #  HELPERS
    # 
    
    def _is_on_cooldown(self, command_name: str, cooldown_seconds: int = 30) -> bool:
        """Verifica si un comando está en cooldown de errores (anti-spam)."""
        now = datetime.now()
        last_error = self.error_cooldown.get(command_name)
        if last_error and (now - last_error).total_seconds() < cooldown_seconds:
            return True
        self.error_cooldown[command_name] = now
        return False

    def read_relevant_files(self, instruction: str) -> str:
        """Lee archivos .py mencionados en la instrucción para dar contexto."""
        context = ""
        files = re.findall(r'\b[\w-]+\.py\b', instruction)
        
        for filename in files:
            path = None
            if (COMMANDS_DIR / filename).exists():
                path = COMMANDS_DIR / filename
            elif (Path(__file__).parent.parent / filename).exists():
                path = Path(__file__).parent.parent / filename
            
            if path:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        context += f"\n--- {filename} ---\n```python\n{content}\n```\n"
                except Exception as e:
                    print(f"Error leyendo {filename}: {e}")
        
        return context

    async def analyze_intent(self, instruction: str) -> dict:
        """Analiza la intención del usuario con IA."""
        if not architect_client:
            return {"action": "create", "target_file": None, "command_name": "nuevo_comando", "description": instruction}
        
        try:
            prompt = INTENT_PROMPT.format(instruction=instruction)
            response = architect_client.models.generate_content(
                model='gemini-3.1-flash-lite-preview',
                contents=prompt
            )
            
            if response.text:
                clean = response.text.strip()
                # Limpiar posible markdown
                if clean.startswith("```"):
                    clean = clean.split("```")[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                clean = clean.strip()
                return json.loads(clean)
        except Exception as e:
            print(f"Error analizando intención: {e}")
        
        return {"action": "create", "target_file": None, "command_name": "nuevo_comando", "description": instruction}

    async def generate_code(self, instruction: str, existing_code: str = None) -> tuple:
        """Genera o modifica código con la IA."""
        if not architect_client:
            return None, " El Arquitecto está dormido (falta API key)"
        
        try:
            context = ""
            if existing_code:
                context = f"\nCÓDIGO ACTUAL A MODIFICAR:\n```python\n{existing_code}\n```\n"
            
            extra_context = self.read_relevant_files(instruction)
            if extra_context:
                context += f"\nCONTEXTO ADICIONAL:\n{extra_context}\n"
            
            full_prompt = f"{ARCHITECT_PROMPT}\n{context}\nSOLICITUD: {instruction}\n\nGenera el código:"
            
            response = architect_client.models.generate_content(
                model='gemini-3.1-flash-lite-preview',
                contents=full_prompt
            )
            
            if response.text:
                code = safe_editor.extract_code_from_markdown(response.text)
                return code, response.text
            return None, "Gemini no generó respuesta"
        except Exception as e:
            return None, f"Error generando código: {e}"

    async def generate_diagnosis(self, error_tb: str, source_code: str, 
                                  command_name: str, severity: ErrorSeverity,
                                  file_name: str = "desconocido") -> tuple:
        """Genera diagnóstico y parche para un error."""
        if not architect_client:
            return None, "El Arquitecto está dormido"
        
        try:
            prompt = DIAGNOSIS_PROMPT.format(
                architect_prompt=ARCHITECT_PROMPT,
                command_name=command_name,
                error_type=type(Exception).__name__,
                severity=ErrorClassifier.get_severity_label(severity),
                error_tb=error_tb[:2000],
                source_code=source_code[:3000],
                file_name=file_name
            )
            
            response = architect_client.models.generate_content(
                model='gemini-3.1-flash-lite-preview',
                contents=prompt
            )
            
            if response.text:
                code = safe_editor.extract_code_from_markdown(response.text)
                return code, response.text
            return None, "No se pudo generar diagnóstico"
        except Exception as e:
            return None, f"Error en diagnóstico: {e}"

    # 
    #  COMANDOS DE CREACIÓN
    # 
    
    @commands.command(name="nuevo", aliases=["new", "create", "crear"])
    @commands.is_owner()
    async def create_new_command(self, ctx, *, instruction: str = None):
        """
        Crea un comando nuevo con IA.
        
        Uso: ¿nuevo <descripción del comando>
        Ejemplo: ¿nuevo un comando de dados que tire 1-6
        """
        if not instruction:
            embed = discord.Embed(
                title=" Falta la instrucción",
                description="**Uso:** `¿nuevo <descripción>`\n**Ejemplo:** `¿nuevo un comando de dados que tire 1-6`",
                color=discord.Color.red()
            )
            return await ctx.reply(embed=embed)
        
        thinking = await ctx.reply(random.choice(PHRASES["thinking"]))
        
        async with ctx.typing():
            # Determinar nombre del archivo
            intent = await self.analyze_intent(instruction)
            cmd_name = intent.get("command_name", "nuevo_comando")
            cmd_name = re.sub(r'[^a-zA-Z0-9_]', '', cmd_name)
            self.pending_file_name = f"{cmd_name}.py"
            self.pending_action = "create"
            
            # Generar código
            code, full_response = await self.generate_code(instruction)
            
            if code:
                safe_editor.write_staged_code(code, "propuesta.py")
                
                embed = discord.Embed(
                    title=f" Nuevo Comando: `{self.pending_file_name}`",
                    color=discord.Color.green()
                )
                
                # Preview inteligente
                lines = code.split('\n')
                preview = '\n'.join(lines[:40])
                if len(lines) > 40:
                    preview += f"\n# ... ({len(lines) - 40} líneas más)"
                
                embed.add_field(
                    name=f" Preview ({len(lines)} líneas)",
                    value=f"```python\n{preview[:1800]}\n```",
                    inline=False
                )
                embed.add_field(
                    name=" Acciones",
                    value="`¿ok` aplicar • `¿ver` ver completo • `¿no` descartar",
                    inline=False
                )
                
                await thinking.edit(content=None, embed=embed)
            else:
                await thinking.edit(content=f"{random.choice(PHRASES['error'])}{full_response[:500]}")

    @commands.command(name="editar", aliases=["edit", "modificar", "mod"])
    @commands.is_owner()
    async def edit_command(self, ctx, *, instruction: str = None):
        """
        Edita un archivo existente con IA.
        
        Uso: ¿editar <archivo.py> <descripción de cambios>
        Ejemplo: ¿editar gatos.py arregla el error de timeout
        """
        if not instruction:
            embed = discord.Embed(
                title=" Falta la instrucción",
                description=(
                    "**Uso:** `¿editar <archivo.py> <cambios>`\n"
                    "**Ejemplo:** `¿editar gatos.py arregla el error de timeout`\n\n"
                    "**Archivos disponibles:**"
                ),
                color=discord.Color.red()
            )
            
            # Listar archivos disponibles
            files = [f.name for f in COMMANDS_DIR.glob("*.py") if f.name != "__init__.py"]
            root_files = [f.name for f in Path(__file__).parent.parent.glob("*.py")]
            all_files = sorted(set(files + root_files))
            embed.add_field(name=" Archivos", value="`" + "`, `".join(all_files) + "`", inline=False)
            
            return await ctx.reply(embed=embed)
        
        thinking = await ctx.reply(random.choice(PHRASES["thinking"]))
        
        async with ctx.typing():
            # Buscar archivo mencionado
            intent = await self.analyze_intent(instruction)
            target_file = intent.get("target_file")
            
            if not target_file:
                # Intentar extraer nombre de archivo de la instrucción
                file_match = re.search(r'\b([\w-]+\.py)\b', instruction)
                target_file = file_match.group(1) if file_match else None
            
            if not target_file:
                await thinking.edit(content=" No pude identificar qué archivo editar. Especifica el nombre: `¿editar archivo.py cambios`")
                return
            
            # Buscar el archivo
            file_path = COMMANDS_DIR / target_file
            if not file_path.exists():
                file_path = Path(__file__).parent.parent / target_file
            
            if not file_path.exists():
                await thinking.edit(content=f" No encontré el archivo `{target_file}`")
                return
            
            # Leer código existente
            with open(file_path, 'r', encoding='utf-8') as f:
                existing_code = f.read()
            
            self.pending_file_name = target_file
            self.pending_action = "edit"
            
            # Generar código modificado
            code, full_response = await self.generate_code(instruction, existing_code)
            
            if code:
                safe_editor.write_staged_code(code, "propuesta.py")
                
                embed = discord.Embed(
                    title=f" Edición: `{target_file}`",
                    color=discord.Color.blue()
                )
                
                # Mostrar info del cambio
                old_lines = len(existing_code.split('\n'))
                new_lines = len(code.split('\n'))
                diff = new_lines - old_lines
                diff_str = f"+{diff}" if diff > 0 else str(diff)
                
                embed.add_field(name=" Cambios", value=f"Antes: {old_lines} líneas → Después: {new_lines} líneas ({diff_str})", inline=False)
                
                # Preview
                lines = code.split('\n')
                preview = '\n'.join(lines[:35])
                if len(lines) > 35:
                    preview += f"\n# ... ({len(lines) - 35} líneas más)"
                
                embed.add_field(
                    name=" Preview",
                    value=f"```python\n{preview[:1500]}\n```",
                    inline=False
                )
                embed.add_field(
                    name=" Acciones",
                    value="`¿ok` aplicar • `¿ver` ver completo • `¿no` descartar",
                    inline=False
                )
                
                await thinking.edit(content=None, embed=embed)
            else:
                await thinking.edit(content=f"{random.choice(PHRASES['error'])}{full_response[:500]}")

    # 
    #  COMANDOS DE STAGING
    # 
    
    @commands.command(name="ok", aliases=["si", "aplicar", "confirmar"])
    @commands.is_owner()
    async def confirm_code(self, ctx):
        """Aplica el código en staging al archivo destino."""
        staged = safe_editor.get_staged_code()
        if not staged:
            return await ctx.reply(" No hay código pendiente.")
        
        target_name = self.pending_file_name or "nuevo_comando.py"
        
        # Determinar ruta del archivo
        if self.pending_action == "edit":
            target_path = COMMANDS_DIR / target_name
            if not target_path.exists():
                target_path = Path(__file__).parent.parent / target_name
        else:
            target_path = COMMANDS_DIR / target_name
        
        async with ctx.typing():
            success, message = safe_editor.apply_code(str(target_path))
            
            if success:
                safe_editor.clear_staging()
                
                embed = discord.Embed(
                    title=" Código Aplicado",
                    description=f"`{target_name}` ha sido {'actualizado' if self.pending_action == 'edit' else 'creado'}.",
                    color=discord.Color.green()
                )
                
                # Hot reload
                reload_msg = ""
                try:
                    cog_name = f"commands.{Path(target_name).stem}"
                    if cog_name in self.bot.extensions:
                        await self.bot.reload_extension(cog_name)
                        reload_msg = f" `{cog_name}` recargado automáticamente"
                    else:
                        await self.bot.load_extension(cog_name)
                        reload_msg = f" `{cog_name}` cargado"
                except Exception as e:
                    reload_msg = f" No pude recargar: {e}\nUsa `¿reiniciar`"
                
                embed.add_field(name="Estado", value=reload_msg, inline=False)
                await ctx.reply(embed=embed)
                
                self.pending_file_name = None
                self.pending_action = None
            else:
                embed = discord.Embed(
                    title=" Error al Aplicar",
                    description=f"```\n{message[:1500]}\n```",
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
    
    @commands.command(name="ver", aliases=["propuesta", "preview", "código"])
    @commands.is_owner()
    async def view_code(self, ctx):
        """Muestra el código completo en staging."""
        code = safe_editor.get_staged_code()
        if not code:
            return await ctx.reply(" No hay código pendiente.")
        
        # Header
        target = self.pending_file_name or "propuesta.py"
        action = "Edición" if self.pending_action == "edit" else "Nuevo"
        await ctx.send(f"** {action}: `{target}` ({len(code.split(chr(10)))} líneas)**")
        
        # Enviar en chunks
        chunks = [code[i:i+1900] for i in range(0, len(code), 1900)]
        for chunk in chunks:
            await ctx.send(f"```python\n{chunk}\n```")
    
    @commands.command(name="no", aliases=["descartar", "cancelar"])
    @commands.is_owner()
    async def discard_code(self, ctx):
        """Descarta el código en staging."""
        if safe_editor.get_staged_code():
            safe_editor.clear_staging()
            self.pending_file_name = None
            self.pending_action = None
            await ctx.reply(" Código descartado.")
        else:
            await ctx.reply(" No había nada que descartar.")

    # 
    #  SISTEMA DE PARCHES
    # 
    
    @commands.command(name="parches", aliases=["patches", "errores"])
    @commands.is_owner()
    async def list_patches(self, ctx):
        """Lista todos los parches pendientes con su severidad."""
        if not self.pending_patches:
            embed = discord.Embed(
                title=" Sin Parches Pendientes",
                description="Todo en orden. No hay errores registrados.",
                color=discord.Color.green()
            )
            if self.auto_fix_count > 0:
                embed.set_footer(text=f" {self.auto_fix_count} reparaciones automáticas realizadas esta sesión")
            return await ctx.reply(embed=embed)
        
        embed = discord.Embed(
            title=f" Parches Pendientes ({len(self.pending_patches)})",
            color=discord.Color.orange()
        )
        
        for pid, patch in self.pending_patches.items():
            severity = patch.get('severity', ErrorSeverity.SUGGEST_FIX)
            emoji = ErrorClassifier.get_severity_emoji(severity)
            label = ErrorClassifier.get_severity_label(severity)
            
            error_preview = patch['error'][:150] + "..." if len(patch['error']) > 150 else patch['error']
            
            embed.add_field(
                name=f"{emoji} #{pid} — `{Path(patch['file']).name}` [{label}]",
                value=f"```\n{error_preview}\n```\n`¿fix {pid}` aplicar • `¿explica {pid}` detalles",
                inline=False
            )
        
        if self.auto_fix_count > 0:
            embed.set_footer(text=f" {self.auto_fix_count} reparaciones automáticas esta sesión")
        
        await ctx.reply(embed=embed)
    
    @commands.command(name="fix", aliases=["arreglar", "parche"])
    @commands.is_owner()
    async def apply_patch(self, ctx, patch_id: int = None):
        """Aplica un parche específico o el primero disponible."""
        if patch_id is None:
            if not self.pending_patches:
                return await ctx.reply(" No hay parches pendientes.")
            patch_id = list(self.pending_patches.keys())[0]
        
        if patch_id not in self.pending_patches:
            return await ctx.reply(f" No existe el parche #{patch_id}. Usa `¿parches` para ver la lista.")
        
        patch = self.pending_patches[patch_id]
        target_path = patch['file']
        
        safe_editor.write_staged_code(patch['code'], "parche_autorepair.py")
        
        async with ctx.typing():
            success, message = safe_editor.apply_code(target_path, str(STAGING_DIR / "parche_autorepair.py"))
            
            if success:
                del self.pending_patches[patch_id]
                
                embed = discord.Embed(
                    title=f" Parche #{patch_id} Aplicado",
                    description=f"`{Path(target_path).name}` reparado.",
                    color=discord.Color.green()
                )
                
                # Hot reload
                try:
                    cog_name = f"commands.{Path(target_path).stem}"
                    if cog_name in self.bot.extensions:
                        await self.bot.reload_extension(cog_name)
                        embed.add_field(name="", value=f"`{cog_name}` recargado", inline=False)
                except Exception as e:
                    embed.add_field(name="", value=f"Error recargando: {e}", inline=False)
                
                await ctx.reply(embed=embed)
            else:
                embed = discord.Embed(
                    title=f" Error aplicando parche #{patch_id}",
                    description=f"```\n{message[:1500]}\n```",
                    color=discord.Color.red()
                )
                await ctx.reply(embed=embed)
    
    @commands.command(name="explica", aliases=["explain"])
    @commands.is_owner()
    async def explain_error(self, ctx, patch_id: int = None):
        """Explica un error/parche con ayuda de la IA."""
        if patch_id is None:
            if not self.pending_patches:
                return await ctx.reply(" No hay parches para explicar.")
            patch_id = list(self.pending_patches.keys())[0]
        
        if patch_id not in self.pending_patches:
            return await ctx.reply(f" No existe el parche #{patch_id}.")
        
        patch = self.pending_patches[patch_id]
        
        async with ctx.typing():
            if not architect_client:
                return await ctx.reply(" El Arquitecto está dormido (falta API key).")
            
            try:
                file_content = safe_editor.read_file_safe(patch['file']) or "No disponible"
                
                prompt = f"""
                {POMPOSO_PERSONALITY}
                
                Explica de forma simple qué pasó y qué hace el parche sugerido:
                
                ERROR: {patch['error']}
                TRACEBACK: {patch.get('traceback', 'No disponible')[:1500]}
                ARCHIVO ({Path(patch['file']).name}):
                ```python
                {file_content[:2000]}
                ```
                
                Explícalo en español, breve y claro.
                """
                
                response = architect_client.models.generate_content(
                    model='gemini-3.1-flash-lite-preview',
                    contents=prompt
                )
                
                explanation = response.text if response.text else "No pude analizarlo."
            except Exception as e:
                explanation = f"Error: {e}"
            
            severity = patch.get('severity', ErrorSeverity.SUGGEST_FIX)
            emoji = ErrorClassifier.get_severity_emoji(severity)
            
            embed = discord.Embed(
                title=f"{emoji} Explicación — Parche #{patch_id}",
                description=explanation[:2000],
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Severidad: {ErrorClassifier.get_severity_label(severity)}")
            await ctx.reply(embed=embed)

    # 
    #  UTILIDADES
    # 
    
    @commands.command(name="undo", aliases=["restaurar", "restore"])
    @commands.is_owner()
    async def restore_backup(self, ctx, archivo: str = None):
        """Restaura un archivo desde su backup más reciente."""
        if not archivo:
            return await ctx.reply(" Especifica: `¿undo nombre.py`")
        
        target = COMMANDS_DIR / archivo
        if not target.exists():
            target = Path(__file__).parent.parent / archivo
        
        success, msg = safe_editor.restore_latest_backup(str(target))
        
        if success:
            embed = discord.Embed(title=" Restaurado", description=msg, color=discord.Color.green())
            # Hot reload
            try:
                cog_name = f"commands.{Path(archivo).stem}"
                if cog_name in self.bot.extensions:
                    await self.bot.reload_extension(cog_name)
                    embed.add_field(name="", value=f"`{cog_name}` recargado", inline=False)
            except:
                pass
            await ctx.reply(embed=embed)
        else:
            await ctx.reply(msg)
    
    @commands.command(name="backups", aliases=["respaldos"])
    @commands.is_owner()
    async def list_backups_cmd(self, ctx, archivo: str = None):
        """Lista backups disponibles."""
        backups = safe_editor.list_backups(archivo)
        if not backups:
            return await ctx.reply(" No hay backups.")
        
        embed = discord.Embed(title=" Backups Disponibles", color=discord.Color.blue())
        for b in backups[:10]:
            embed.add_field(name=b['name'], value=f" {b['date']}", inline=True)
        
        if len(backups) > 10:
            embed.set_footer(text=f"... y {len(backups)-10} más")
        await ctx.reply(embed=embed)
    
    @commands.command(name="historial", aliases=["history", "cambios"])
    @commands.is_owner()
    async def show_history(self, ctx):
        """Muestra el historial de cambios recientes."""
        history = safe_editor.get_history()
        if not history:
            return await ctx.reply(" No hay cambios registrados en esta sesión.")
        
        embed = discord.Embed(
            title=" Historial de Cambios",
            description=f"Últimos {len(history)} cambios de esta sesión:",
            color=discord.Color.blue()
        )
        
        for entry in history:
            action_emoji = {"auto-fix": "", "manual": ""}.get(entry['action'], "")
            backup_info = f" (backup: `{entry['backup']}`)" if entry['backup'] else ""
            embed.add_field(
                name=f"{action_emoji} {entry['file']}",
                value=f" {entry['timestamp']} — {entry['action']}{backup_info}",
                inline=False
            )
        
        if self.auto_fix_count > 0:
            embed.set_footer(text=f" Total auto-reparaciones esta sesión: {self.auto_fix_count}")
        
        await ctx.reply(embed=embed)
    
    @commands.command(name="reiniciar", aliases=["restart", "reboot"])
    @commands.is_owner()
    async def restart_bot(self, ctx):
        """Reinicia el bot."""
        await ctx.reply(" Reiniciando...")
        sys.exit(0)

    # 
    #  AYUDA DEL ARQUITECTO
    # 
    
    @commands.command(name="arquitecto", aliases=["architect", "arch"])
    @commands.is_owner()
    async def architect_help(self, ctx):
        """Muestra la guía completa del Arquitecto."""
        embed = discord.Embed(
            title=" Arquitecto V3 — Guía de Comandos",
            description="Sistema inteligente de código con auto-reparación.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name=" Creación",
            value=(
                "`¿nuevo <instrucción>` — Crear comando nuevo\n"
                "`¿editar <archivo.py> <cambios>` — Editar archivo existente"
            ),
            inline=False
        )
        
        embed.add_field(
            name=" Staging",
            value=(
                "`¿ok` — Aplicar código pendiente\n"
                "`¿ver` — Ver código completo\n"
                "`¿no` — Descartar código"
            ),
            inline=False
        )
        
        embed.add_field(
            name=" Parches",
            value=(
                "`¿parches` — Ver parches pendientes\n"
                "`¿fix [id]` — Aplicar un parche\n"
                "`¿explica [id]` — Explicar un error"
            ),
            inline=False
        )
        
        embed.add_field(
            name=" Utilidades",
            value=(
                "`¿undo <archivo>` — Restaurar desde backup\n"
                "`¿backups [archivo]` — Ver backups\n"
                "`¿historial` — Historial de cambios\n"
                "`¿reiniciar` — Reiniciar bot"
            ),
            inline=False
        )
        
        embed.add_field(
            name=" Auto-Reparación",
            value=(
                "El sistema clasifica errores automáticamente:\n"
                " **AUTO_FIX** — Se repara solo (imports, sintaxis)\n"
                " **SUGGEST_FIX** — Genera parche, pide confirmación\n"
                " **NOTIFY_ONLY** — Solo notifica (errores externos)"
            ),
            inline=False
        )
        
        if self.auto_fix_count > 0:
            embed.set_footer(text=f" {self.auto_fix_count} auto-reparaciones esta sesión")
        
        await ctx.reply(embed=embed)

    # 
    #  AUTO-DIAGNÓSTICO INTELIGENTE
    # 
    
    async def handle_error_diagnosis(self, ctx, error):
        """
        Sistema inteligente de auto-reparación.
        
        Flujo:
        1. Clasificar error (AUTO_FIX / SUGGEST_FIX / NOTIFY_ONLY)
        2. Si AUTO_FIX: reparar automáticamente sin preguntar
        3. Si SUGGEST_FIX: generar parche y notificar al dueño
        4. Si NOTIFY_ONLY: solo notificar que algo falló
        """
        # Ignorar errores que ya se manejan
        if isinstance(error, (commands.CommandNotFound, commands.MissingPermissions, 
                             commands.NotOwner, commands.MissingRequiredArgument)):
            return
        
        original = getattr(error, 'original', error)
        error_tb = ''.join(traceback.format_exception(type(original), original, original.__traceback__))
        
        command_name = ctx.command.qualified_name if ctx.command else "desconocido"
        
        # Rate limiting: no generar parches si el mismo comando falla repetidamente
        if self._is_on_cooldown(command_name, cooldown_seconds=30):
            print(f" Error en '{command_name}' en cooldown, ignorando.")
            return
        
        # Obtener código fuente
        source_code = "No disponible"
        source_file = None
        
        if ctx.command and ctx.command.callback:
            try:
                source_code = inspect.getsource(ctx.command.callback)
                source_file = inspect.getfile(ctx.command.callback)
            except:
                pass
        
        if not source_file:
            return
        
        #  PASO 1: Clasificar el error 
        severity = ErrorClassifier.classify(original, error_tb)
        severity_emoji = ErrorClassifier.get_severity_emoji(severity)
        severity_label = ErrorClassifier.get_severity_label(severity)
        
        print(f"\n{severity_emoji} Error clasificado como [{severity_label}] en '{command_name}'")
        
        #  PASO 2: Actuar según la severidad 
        
        if severity == ErrorSeverity.NOTIFY_ONLY:
            # Solo notificar al dueño, no intentar reparar
            await self._notify_owner_error(command_name, original, error_tb, severity, source_file)
            return
        
        # Para AUTO_FIX y SUGGEST_FIX, generar parche
        # Leer archivo completo para dar contexto
        full_source = safe_editor.read_file_safe(source_file) or source_code
        
        patch_code, explanation = await self.generate_diagnosis(
            error_tb, full_source, command_name, severity, Path(source_file).name
        )
        
        if not patch_code:
            await self._notify_owner_error(command_name, original, error_tb, severity, source_file)
            return
        
        if severity == ErrorSeverity.AUTO_FIX:
            #  AUTO-REPARACIÓN 
            await self._auto_fix(ctx, command_name, patch_code, source_file, original, explanation)
        else:
            #  SUGGEST_FIX: guardar parche y notificar 
            self.patch_counter += 1
            patch_id = self.patch_counter
            self.pending_patches[patch_id] = {
                "error": str(original),
                "code": patch_code,
                "file": source_file,
                "traceback": error_tb,
                "severity": severity,
            }
            
            await self._notify_owner_patch(command_name, original, explanation, patch_id, severity, source_file)
    
    async def _auto_fix(self, ctx, command_name, patch_code, source_file, error, explanation):
        """Aplica un parche automáticamente sin pedir confirmación."""
        print(f" Intentando auto-reparación de '{command_name}'...")
        
        # Guardar parche en staging
        safe_editor.write_staged_code(patch_code, "parche_autorepair.py")
        
        # Aplicar
        success, message = safe_editor.apply_code(
            source_file, 
            str(STAGING_DIR / "parche_autorepair.py"),
            auto_fix=True
        )
        
        if success:
            self.auto_fix_count += 1
            
            # Hot reload
            reload_ok = False
            try:
                cog_name = f"commands.{Path(source_file).stem}"
                if cog_name in self.bot.extensions:
                    await self.bot.reload_extension(cog_name)
                    reload_ok = True
            except Exception as e:
                print(f" Error recargando tras auto-fix: {e}")
            
            # Notificar al dueño del éxito
            try:
                owner = await self.bot.fetch_user(self.bot.owner_id)
                embed = discord.Embed(
                    title=f" Auto-Reparación Exitosa — `{command_name}`",
                    description=f"Se detectó y reparó automáticamente un error.",
                    color=discord.Color.green()
                )
                embed.add_field(name="Error Original", value=f"```\n{str(error)[:300]}\n```", inline=False)
                embed.add_field(name="Estado", value=f"{' Recargado' if reload_ok else ' Necesita reinicio'}", inline=True)
                embed.add_field(name="Reparaciones Auto", value=f"#{self.auto_fix_count}", inline=True)
                embed.set_footer(text="Usa ¿undo para revertir si algo no se ve bien")
                await owner.send(embed=embed)
            except:
                pass
            
            # Informar en el canal
            await ctx.send(f" Error detectado y reparado automáticamente en `{command_name}`. Intenta de nuevo.")
            
            print(f" Auto-reparación #{self.auto_fix_count} exitosa para '{command_name}'")
        else:
            # Si auto-fix falla, degradar a SUGGEST_FIX
            print(f" Auto-reparación falló para '{command_name}', guardando como parche sugerido")
            self.patch_counter += 1
            self.pending_patches[self.patch_counter] = {
                "error": str(error),
                "code": patch_code,
                "file": source_file,
                "traceback": "",
                "severity": ErrorSeverity.SUGGEST_FIX,
            }
            await self._notify_owner_patch(
                command_name, error, explanation, 
                self.patch_counter, ErrorSeverity.SUGGEST_FIX, source_file
            )
    
    async def _notify_owner_error(self, command_name, error, error_tb, severity, source_file):
        """Notifica al dueño sobre un error que no se puede reparar."""
        try:
            owner = await self.bot.fetch_user(self.bot.owner_id)
            emoji = ErrorClassifier.get_severity_emoji(severity)
            label = ErrorClassifier.get_severity_label(severity)
            
            embed = discord.Embed(
                title=f"{emoji} Error en `{command_name}` [{label}]",
                description=f"```\n{str(error)[:500]}\n```",
                color=discord.Color.red()
            )
            
            if severity == ErrorSeverity.NOTIFY_ONLY:
                embed.add_field(
                    name="ℹ ¿Por qué no se puede reparar?",
                    value="Este error es externo (API, red, permisos) y no se puede resolver editando código.",
                    inline=False
                )
            
            embed.add_field(name="Archivo", value=f"`{Path(source_file).name}`", inline=True)
            embed.set_footer(text=f"Traceback completo en la consola del bot")
            
            await owner.send(embed=embed)
        except Exception as e:
            print(f"Error enviando DM al dueño: {e}")
    
    async def _notify_owner_patch(self, command_name, error, explanation, patch_id, severity, source_file):
        """Notifica al dueño sobre un parche sugerido."""
        try:
            owner = await self.bot.fetch_user(self.bot.owner_id)
            emoji = ErrorClassifier.get_severity_emoji(severity)
            label = ErrorClassifier.get_severity_label(severity)
            
            embed = discord.Embed(
                title=f"{emoji} Error en `{command_name}` — Parche #{patch_id} [{label}]",
                description=f"```\n{str(error)[:400]}\n```",
                color=discord.Color.orange()
            )
            
            if explanation:
                embed.add_field(
                    name=" Análisis",
                    value=explanation[:500],
                    inline=False
                )
            
            embed.add_field(name="Archivo", value=f"`{Path(source_file).name}`", inline=True)
            embed.add_field(
                name="Acciones",
                value=f"`¿fix {patch_id}` aplicar • `¿explica {patch_id}` detalles",
                inline=False
            )
            
            await owner.send(embed=embed)
        except Exception as e:
            print(f"Error enviando DM al dueño: {e}")

    # 
    #  SLASH COMMANDS
    # 
    
    owner_group = app_commands.Group(name="owner", description="Comandos privados del sistema (Solo Dueño)")
    
    @owner_group.command(name="nuevo", description="Crear comando nuevo con IA")
    @app_commands.describe(instruccion="Descripción del comando a crear")
    async def nuevo_slash(self, interaction: discord.Interaction, instruccion: str):
        if interaction.user.id != self.bot.owner_id:
            return await interaction.response.send_message("", ephemeral=True)
        ctx = await self.bot.get_context(interaction)
        await self.create_new_command(ctx, instruction=instruccion)

    @owner_group.command(name="editar", description="Editar archivo existente con IA")
    @app_commands.describe(instruccion="Archivo y cambios a realizar")
    async def editar_slash(self, interaction: discord.Interaction, instruccion: str):
        if interaction.user.id != self.bot.owner_id:
            return await interaction.response.send_message("", ephemeral=True)
        ctx = await self.bot.get_context(interaction)
        await self.edit_command(ctx, instruction=instruccion)

    @owner_group.command(name="ok", description="Confirmar y aplicar código")
    async def ok_slash(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.confirm_code(ctx)

    @owner_group.command(name="ver", description="Ver código pendiente")
    async def ver_slash(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.view_code(ctx)

    @owner_group.command(name="no", description="Descartar código")
    async def no_slash(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.discard_code(ctx)

    @owner_group.command(name="parches", description="Ver parches pendientes")
    async def parches_slash(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.list_patches(ctx)

    @owner_group.command(name="fix", description="Aplicar un parche")
    @app_commands.describe(id="ID del parche")
    async def fix_slash(self, interaction: discord.Interaction, id: int):
        ctx = await self.bot.get_context(interaction)
        await self.apply_patch(ctx, patch_id=id)

    @owner_group.command(name="explica", description="Explicar un error")
    @app_commands.describe(id="ID del parche")
    async def explica_slash(self, interaction: discord.Interaction, id: int):
        ctx = await self.bot.get_context(interaction)
        await self.explain_error(ctx, patch_id=id)

    @owner_group.command(name="undo", description="Restaurar archivo desde backup")
    @app_commands.describe(archivo="Nombre del archivo a restaurar")
    async def undo_slash(self, interaction: discord.Interaction, archivo: str):
        ctx = await self.bot.get_context(interaction)
        await self.restore_backup(ctx, archivo=archivo)

    @owner_group.command(name="reiniciar", description="Reiniciar el bot")
    async def restart_slash(self, interaction: discord.Interaction):
        ctx = await self.bot.get_context(interaction)
        await self.restart_bot(ctx)


async def setup(bot):
    await bot.add_cog(ArchitectCog(bot))
    print(" Arquitecto V3 cargado.")
