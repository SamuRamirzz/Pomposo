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
  ¿arquitecto            → Muestra esta ayuda
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
from pathlib import Path
from openrouter import chat_completion

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from safe_editor import safe_editor, ErrorClassifier, ErrorSeverity, COMMANDS_DIR, STAGING_DIR

print(" Arquitecto V3 inicializado.")

# --- Personalidad ---
PERSONALITY_FILE = Path(__file__).parent.parent / "ask_personalidad.txt"

def load_personality() -> str:
    try:
        if PERSONALITY_FILE.exists():
            with open(PERSONALITY_FILE, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception:
        pass
    return "Eres Pomposo, una IA sarcástica."

POMPOSO_PERSONALITY = load_personality()

# ─────────────────────────────────────────────
# PROMPTS — Sin f-strings anidados para evitar errores de llaves
# ─────────────────────────────────────────────

# Se construye con .format() en el momento de uso, NO como f-string global,
# porque POMPOSO_PERSONALITY puede contener llaves {} que rompen f-strings.
ARCHITECT_PROMPT_TEMPLATE = (
    "ROL: Eres el 'Arquitecto', el lado técnico de Pomposo.\n"
    "Escribes y modificas código Python para un bot de Discord (discord.py).\n\n"
    "TU PERSONALIDAD:\n"
    "{personality}\n\n"
    "CONTEXTO TÉCNICO:\n"
    "- Python asíncrono (async/await), Cogs (commands.Cog)\n"
    "- Prefijo del bot: ¿\n"
    "- Comentarios sarcásticos estilo Pomposo\n"
    "- NUNCA elimines @commands.is_owner() de comandos protegidos\n"
    "- Incluye SIEMPRE async def setup(bot) al final\n\n"
    "INSTRUCCIONES:\n"
    "1. Devuelve SOLO código Python válido en bloque ```python\n"
    "2. Para comandos nuevos: estructura completa de Cog con setup()\n"
    "3. Para ediciones: archivo completo modificado\n"
    "4. Importa TODAS las librerías necesarias\n"
)

# Prompt para clasificar intención — las llaves dobles {{ }} son literales en .format()
INTENT_PROMPT_TEMPLATE = (
    'Analiza esta solicitud y determina la intención:\n\n'
    'SOLICITUD: "{instruction}"\n\n'
    'Responde SOLO con un JSON válido sin markdown ni explicaciones:\n'
    '{{"action": "create" o "edit", '
    '"target_file": "nombre_archivo.py" o null, '
    '"command_name": "nombre_comando" o null, '
    '"description": "breve descripción"}}'
)

# Prompt de diagnóstico — también con .format() en el momento de uso
DIAGNOSIS_PROMPT_TEMPLATE = (
    "MODO: DIAGNÓSTICO Y REPARACIÓN\n\n"
    "El comando '{command_name}' falló con este error:\n\n"
    "TIPO DE ERROR: {error_type}\n"
    "SEVERIDAD: {severity}\n\n"
    "TRACEBACK:\n"
    "```\n{error_tb}\n```\n\n"
    "CÓDIGO ACTUAL ({file_name}):\n"
    "```python\n{source_code}\n```\n\n"
    "INSTRUCCIONES:\n"
    "1. Analiza qué causó el error\n"
    "2. Genera el código COMPLETO del archivo corregido en bloque ```python\n"
    "3. Mantén toda la funcionalidad existente\n"
    "4. Corrige SOLO lo necesario para resolver el error\n"
)

# Prompt para explicar errores — sin variables externas problemáticas
EXPLAIN_PROMPT_TEMPLATE = (
    "{personality}\n\n"
    "Explica de forma simple qué pasó y qué hace el parche sugerido.\n"
    "Responde en español, breve y claro, con tu personalidad caótica.\n\n"
    "ERROR: {error}\n"
    "TRACEBACK:\n{traceback}\n\n"
    "ARCHIVO ({filename}):\n"
    "```python\n{source}\n```"
)

def build_architect_prompt() -> str:
    """Construye el prompt del arquitecto con la personalidad actual."""
    return ARCHITECT_PROMPT_TEMPLATE.format(personality=POMPOSO_PERSONALITY)

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
}


# ─────────────────────────────────────────────
# ARCHITECT COG V3
# ─────────────────────────────────────────────

class ArchitectCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending_file_name = None
        self.pending_action = None       # "create" o "edit"
        self.pending_patches = {}        # {id: {error, code, file, traceback, severity}}
        self.patch_counter = 0
        self.error_cooldown = {}         # {command_name: last_error_time}
        self.auto_fix_count = 0

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _is_on_cooldown(self, command_name: str, cooldown_seconds: int = 30) -> bool:
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
        """Analiza la intención del usuario con IA. Devuelve JSON."""
        try:
            prompt = INTENT_PROMPT_TEMPLATE.format(instruction=instruction)
            response_text = await chat_completion(
                system_prompt=(
                    "Eres un clasificador JSON estricto. "
                    "Responde ÚNICAMENTE con un objeto JSON válido, sin backticks, sin texto extra."
                ),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200
            )
            if response_text:
                clean = response_text.strip().strip("```json").strip("```").strip()
                return json.loads(clean)
        except Exception as e:
            print(f"Error analizando intención: {e}")
        return {
            "action": "create",
            "target_file": None,
            "command_name": "nuevo_comando",
            "description": instruction
        }

    async def generate_code(self, instruction: str, existing_code: str = None) -> tuple:
        """Genera o modifica código con la IA. Retorna (code, full_response)."""
        try:
            context = ""
            if existing_code:
                context = f"\nCÓDIGO ACTUAL A MODIFICAR:\n```python\n{existing_code}\n```\n"
            extra_context = self.read_relevant_files(instruction)
            if extra_context:
                context += f"\nCONTEXTO ADICIONAL:\n{extra_context}\n"

            user_prompt = f"{context}\nSOLICITUD: {instruction}\n\nGenera el código:"

            response_text = await chat_completion(
                system_prompt=build_architect_prompt(),
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=4000
            )
            if response_text:
                code = safe_editor.extract_code_from_markdown(response_text)
                return code, response_text
            return None, "La IA no generó respuesta"
        except Exception as e:
            return None, f"Error generando código: {e}"

    async def generate_diagnosis(
        self, error_tb: str, source_code: str,
        command_name: str, severity: ErrorSeverity,
        file_name: str = "desconocido"
    ) -> tuple:
        """Genera diagnóstico y parche para un error. Retorna (code, explanation)."""
        try:
            diagnosis_prompt = DIAGNOSIS_PROMPT_TEMPLATE.format(
                command_name=command_name,
                error_type=severity.value,
                severity=ErrorClassifier.get_severity_label(severity),
                error_tb=error_tb[:2000],
                source_code=source_code[:3000],
                file_name=file_name
            )

            response_text = await chat_completion(
                system_prompt=(
                    build_architect_prompt() + "\n\n"
                    "Diagnostica el error y devuelve el archivo COMPLETO corregido en bloque ```python."
                ),
                messages=[{"role": "user", "content": diagnosis_prompt}],
                max_tokens=4000
            )
            if response_text:
                code = safe_editor.extract_code_from_markdown(response_text)
                return code, response_text
            return None, "No se pudo generar diagnóstico"
        except Exception as e:
            return None, f"Error en diagnóstico: {e}"

    async def generate_explanation(self, patch: dict) -> str:
        """Genera una explicación legible del error y el parche usando chat_completion."""
        try:
            file_content = safe_editor.read_file_safe(patch['file']) or "No disponible"
            prompt = EXPLAIN_PROMPT_TEMPLATE.format(
                personality=POMPOSO_PERSONALITY,
                error=patch['error'],
                traceback=patch.get('traceback', 'No disponible')[:1500],
                filename=Path(patch['file']).name,
                source=file_content[:2000]
            )
            response = await chat_completion(
                system_prompt="Eres el Arquitecto de Pomposo. Explica errores de forma clara y sarcástica.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.7
            )
            return response.strip() if response else "No pude analizarlo."
        except Exception as e:
            return f"Error generando explicación: {e}"

    # ─────────────────────────────────────────────
    # COMANDOS DE CREACIÓN
    # ─────────────────────────────────────────────

    @commands.command(name="nuevo", aliases=["new", "create", "crear"])
    @commands.is_owner()
    async def create_new_command(self, ctx, *, instruction: str = None):
        """Crea un comando nuevo con IA."""
        if not instruction:
            embed = discord.Embed(
                title=" Falta la instrucción",
                description="**Uso:** `¿nuevo <descripción>`\n**Ejemplo:** `¿nuevo un comando de dados que tire 1-6`",
                color=discord.Color.red()
            )
            return await ctx.reply(embed=embed)

        thinking = await ctx.reply(random.choice(PHRASES["thinking"]))

        async with ctx.typing():
            intent = await self.analyze_intent(instruction)
            cmd_name = intent.get("command_name", "nuevo_comando")
            cmd_name = re.sub(r'[^a-zA-Z0-9_]', '', cmd_name) or "nuevo_comando"
            self.pending_file_name = f"{cmd_name}.py"
            self.pending_action = "create"

            code, full_response = await self.generate_code(instruction)

            if code:
                safe_editor.write_staged_code(code, "propuesta.py")
                lines = code.split('\n')
                preview = '\n'.join(lines[:40])
                if len(lines) > 40:
                    preview += f"\n# ... ({len(lines) - 40} líneas más)"

                embed = discord.Embed(
                    title=f" Nuevo Comando: `{self.pending_file_name}`",
                    color=discord.Color.green()
                )
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
        """Edita un archivo existente con IA."""
        if not instruction:
            embed = discord.Embed(
                title=" Falta la instrucción",
                description=(
                    "**Uso:** `¿editar <archivo.py> <cambios>`\n"
                    "**Ejemplo:** `¿editar gatos.py arregla el error de timeout`"
                ),
                color=discord.Color.red()
            )
            files = [f.name for f in COMMANDS_DIR.glob("*.py") if f.name != "__init__.py"]
            root_files = [f.name for f in Path(__file__).parent.parent.glob("*.py")]
            all_files = sorted(set(files + root_files))
            embed.add_field(name=" Archivos", value="`" + "`, `".join(all_files) + "`", inline=False)
            return await ctx.reply(embed=embed)

        thinking = await ctx.reply(random.choice(PHRASES["thinking"]))

        async with ctx.typing():
            intent = await self.analyze_intent(instruction)
            target_file = intent.get("target_file")

            if not target_file:
                file_match = re.search(r'\b([\w-]+\.py)\b', instruction)
                target_file = file_match.group(1) if file_match else None

            if not target_file:
                await thinking.edit(content=" No pude identificar qué archivo editar. Especifica: `¿editar archivo.py cambios`")
                return

            file_path = COMMANDS_DIR / target_file
            if not file_path.exists():
                file_path = Path(__file__).parent.parent / target_file
            if not file_path.exists():
                await thinking.edit(content=f" No encontré el archivo `{target_file}`")
                return

            with open(file_path, 'r', encoding='utf-8') as f:
                existing_code = f.read()

            self.pending_file_name = target_file
            self.pending_action = "edit"

            code, full_response = await self.generate_code(instruction, existing_code)

            if code:
                safe_editor.write_staged_code(code, "propuesta.py")
                old_lines = len(existing_code.split('\n'))
                new_lines = len(code.split('\n'))
                diff = new_lines - old_lines
                diff_str = f"+{diff}" if diff > 0 else str(diff)

                lines = code.split('\n')
                preview = '\n'.join(lines[:35])
                if len(lines) > 35:
                    preview += f"\n# ... ({len(lines) - 35} líneas más)"

                embed = discord.Embed(title=f" Edición: `{target_file}`", color=discord.Color.blue())
                embed.add_field(
                    name=" Cambios",
                    value=f"Antes: {old_lines} líneas → Después: {new_lines} líneas ({diff_str})",
                    inline=False
                )
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

    # ─────────────────────────────────────────────
    # STAGING
    # ─────────────────────────────────────────────

    @commands.command(name="ok", aliases=["si", "aplicar", "confirmar"])
    @commands.is_owner()
    async def confirm_code(self, ctx):
        """Aplica el código en staging al archivo destino."""
        staged = safe_editor.get_staged_code()
        if not staged:
            return await ctx.reply(" No hay código pendiente.")

        target_name = self.pending_file_name or "nuevo_comando.py"

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
                    description=f"`{target_name}` {'actualizado' if self.pending_action == 'edit' else 'creado'}.",
                    color=discord.Color.green()
                )
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
        target = self.pending_file_name or "propuesta.py"
        action = "Edición" if self.pending_action == "edit" else "Nuevo"
        await ctx.send(f"** {action}: `{target}` ({len(code.split(chr(10)))} líneas)**")
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

    # ─────────────────────────────────────────────
    # PARCHES
    # ─────────────────────────────────────────────

    @commands.command(name="parches", aliases=["patches", "errores"])
    @commands.is_owner()
    async def list_patches(self, ctx):
        """Lista todos los parches pendientes."""
        if not self.pending_patches:
            embed = discord.Embed(
                title=" Sin Parches Pendientes",
                description="Todo en orden.",
                color=discord.Color.green()
            )
            if self.auto_fix_count > 0:
                embed.set_footer(text=f" {self.auto_fix_count} reparaciones automáticas esta sesión")
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
        """Aplica un parche específico."""
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
            success, message = safe_editor.apply_code(
                target_path,
                str(STAGING_DIR / "parche_autorepair.py")
            )

            if success:
                del self.pending_patches[patch_id]
                embed = discord.Embed(
                    title=f" Parche #{patch_id} Aplicado",
                    description=f"`{Path(target_path).name}` reparado.",
                    color=discord.Color.green()
                )
                try:
                    cog_name = f"commands.{Path(target_path).stem}"
                    if cog_name in self.bot.extensions:
                        await self.bot.reload_extension(cog_name)
                        embed.add_field(name="", value=f"`{cog_name}` recargado", inline=False)
                except Exception as e:
                    embed.add_field(name="", value=f"Error recargando: {e}", inline=False)
                await ctx.reply(embed=embed)
            else:
                # Mostrar error de validación con detalle para que el dueño sepa qué falló
                embed = discord.Embed(
                    title=f" Error aplicando parche #{patch_id}",
                    description=(
                        f"```\n{message[:1200]}\n```\n\n"
                        "El parche no pasó la validación de sintaxis. "
                        "Usa `¿explica {patch_id}` para que la IA regenere un parche mejor."
                    ),
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="Opciones",
                    value=f"`¿explica {patch_id}` → regenerar parche • `¿parches` → ver lista",
                    inline=False
                )
                await ctx.reply(embed=embed)

    @commands.command(name="explica", aliases=["explain"])
    @commands.is_owner()
    async def explain_error(self, ctx, patch_id: int = None):
        """
        Explica un error y regenera el parche si el anterior falló.
        Ya no usa architect_client — usa chat_completion directamente.
        """
        if patch_id is None:
            if not self.pending_patches:
                return await ctx.reply(" No hay parches para explicar.")
            patch_id = list(self.pending_patches.keys())[0]

        if patch_id not in self.pending_patches:
            return await ctx.reply(f" No existe el parche #{patch_id}.")

        patch = self.pending_patches[patch_id]
        thinking = await ctx.reply(" Analizando el error...")

        async with ctx.typing():
            # 1. Generar explicación
            explanation = await self.generate_explanation(patch)

            # 2. Regenerar parche (el anterior puede haber tenido sintaxis rota)
            full_source = safe_editor.read_file_safe(patch['file']) or "No disponible"
            severity = patch.get('severity', ErrorSeverity.SUGGEST_FIX)

            new_code, _ = await self.generate_diagnosis(
                patch.get('traceback', patch['error']),
                full_source,
                Path(patch['file']).stem,
                severity,
                Path(patch['file']).name
            )

            # 3. Actualizar parche con el código regenerado
            if new_code:
                self.pending_patches[patch_id]['code'] = new_code
                regen_msg = " Parche regenerado con código nuevo."
            else:
                regen_msg = " No se pudo regenerar el parche."

            severity_emoji = ErrorClassifier.get_severity_emoji(severity)
            embed = discord.Embed(
                title=f"{severity_emoji} Explicación — Parche #{patch_id}",
                description=explanation[:2000],
                color=discord.Color.blue()
            )
            embed.add_field(name="Estado del parche", value=regen_msg, inline=False)
            embed.add_field(
                name="Siguiente paso",
                value=f"`¿fix {patch_id}` para aplicar el parche regenerado",
                inline=False
            )
            embed.set_footer(text=f"Severidad: {ErrorClassifier.get_severity_label(severity)}")
            await thinking.edit(content=None, embed=embed)

    # ─────────────────────────────────────────────
    # UTILIDADES
    # ─────────────────────────────────────────────

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
            try:
                cog_name = f"commands.{Path(archivo).stem}"
                if cog_name in self.bot.extensions:
                    await self.bot.reload_extension(cog_name)
                    embed.add_field(name="", value=f"`{cog_name}` recargado", inline=False)
            except Exception:
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
            value="`¿nuevo <instrucción>` — Crear comando nuevo\n`¿editar <archivo.py> <cambios>` — Editar archivo",
            inline=False
        )
        embed.add_field(
            name=" Staging",
            value="`¿ok` — Aplicar\n`¿ver` — Ver código\n`¿no` — Descartar",
            inline=False
        )
        embed.add_field(
            name=" Parches",
            value="`¿parches` — Ver pendientes\n`¿fix [id]` — Aplicar\n`¿explica [id]` — Explicar y regenerar",
            inline=False
        )
        embed.add_field(
            name=" Utilidades",
            value="`¿undo <archivo>` — Restaurar\n`¿backups` — Ver backups\n`¿historial` — Cambios\n`¿reiniciar` — Reiniciar bot",
            inline=False
        )
        embed.add_field(
            name=" Auto-Reparación",
            value=(
                " **AUTO_FIX** — Se repara solo\n"
                " **SUGGEST_FIX** — Genera parche, pide confirmación\n"
                " **NOTIFY_ONLY** — Solo notifica (errores externos)"
            ),
            inline=False
        )
        if self.auto_fix_count > 0:
            embed.set_footer(text=f" {self.auto_fix_count} auto-reparaciones esta sesión")
        await ctx.reply(embed=embed)

    # ─────────────────────────────────────────────
    # AUTO-DIAGNÓSTICO
    # ─────────────────────────────────────────────

    async def handle_error_diagnosis(self, ctx, error):
        """
        Sistema de auto-reparación.
        Flujo: Clasificar → AUTO_FIX (aplica solo) / SUGGEST_FIX (guarda parche) / NOTIFY_ONLY (notifica)
        """
        if isinstance(error, (
            commands.CommandNotFound, commands.MissingPermissions,
            commands.NotOwner, commands.MissingRequiredArgument, commands.BadArgument
        )):
            return

        original = getattr(error, 'original', error)
        error_tb = ''.join(traceback.format_exception(type(original), original, original.__traceback__))
        command_name = ctx.command.qualified_name if ctx.command else "desconocido"

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
            except Exception:
                pass

        if not source_file:
            return

        # Clasificar error
        severity = ErrorClassifier.classify(original, error_tb)
        severity_emoji = ErrorClassifier.get_severity_emoji(severity)
        severity_label = ErrorClassifier.get_severity_label(severity)
        print(f"\n{severity_emoji} Error [{severity_label}] en '{command_name}'")

        if severity == ErrorSeverity.NOTIFY_ONLY:
            await self._notify_owner_error(command_name, original, error_tb, severity, source_file)
            return

        # Generar parche para AUTO_FIX y SUGGEST_FIX
        full_source = safe_editor.read_file_safe(source_file) or source_code
        patch_code, explanation = await self.generate_diagnosis(
            error_tb, full_source, command_name, severity, Path(source_file).name
        )

        if not patch_code:
            await self._notify_owner_error(command_name, original, error_tb, severity, source_file)
            return

        if severity == ErrorSeverity.AUTO_FIX:
            await self._auto_fix(ctx, command_name, patch_code, source_file, original, explanation)
        else:
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
        safe_editor.write_staged_code(patch_code, "parche_autorepair.py")
        success, message = safe_editor.apply_code(
            source_file,
            str(STAGING_DIR / "parche_autorepair.py"),
            auto_fix=True
        )

        if success:
            self.auto_fix_count += 1
            reload_ok = False
            try:
                cog_name = f"commands.{Path(source_file).stem}"
                if cog_name in self.bot.extensions:
                    await self.bot.reload_extension(cog_name)
                    reload_ok = True
            except Exception as e:
                print(f" Error recargando tras auto-fix: {e}")

            try:
                owner = await self.bot.fetch_user(self.bot.owner_id)
                embed = discord.Embed(
                    title=f" Auto-Reparación Exitosa — `{command_name}`",
                    description="Se detectó y reparó automáticamente un error.",
                    color=discord.Color.green()
                )
                embed.add_field(name="Error Original", value=f"```\n{str(error)[:300]}\n```", inline=False)
                embed.add_field(name="Estado", value=" Recargado" if reload_ok else " Necesita reinicio", inline=True)
                embed.add_field(name="Reparaciones Auto", value=f"#{self.auto_fix_count}", inline=True)
                embed.set_footer(text="Usa ¿undo para revertir si algo no se ve bien")
                await owner.send(embed=embed)
            except Exception:
                pass

            await ctx.send(f" Error detectado y reparado automáticamente en `{command_name}`. Intenta de nuevo.")
            print(f" Auto-reparación #{self.auto_fix_count} exitosa para '{command_name}'")
        else:
            # Si el auto-fix falla la validación, degradar a SUGGEST_FIX con detalle del error
            print(f" Auto-reparación falló para '{command_name}': {message}")
            self.patch_counter += 1
            pid = self.patch_counter
            self.pending_patches[pid] = {
                "error": str(error),
                "code": patch_code,
                "file": source_file,
                "traceback": "",
                "severity": ErrorSeverity.SUGGEST_FIX,
                "validation_error": message,  # guardamos el error de validación para diagnóstico
            }
            await self._notify_owner_patch(
                command_name, error, f"Auto-fix falló validación: {message}\n\n{explanation}",
                pid, ErrorSeverity.SUGGEST_FIX, source_file
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
            embed.set_footer(text="Traceback completo en la consola del bot")
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
                embed.add_field(name=" Análisis", value=str(explanation)[:500], inline=False)
            embed.add_field(name="Archivo", value=f"`{Path(source_file).name}`", inline=True)
            embed.add_field(
                name="Acciones",
                value=f"`¿fix {patch_id}` aplicar • `¿explica {patch_id}` explicar y regenerar",
                inline=False
            )
            await owner.send(embed=embed)
        except Exception as e:
            print(f"Error enviando DM al dueño: {e}")

    # ─────────────────────────────────────────────
    # SLASH COMMANDS
    # ─────────────────────────────────────────────

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

    @owner_group.command(name="explica", description="Explicar y regenerar un parche")
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