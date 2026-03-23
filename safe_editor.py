"""
SAFE EDITOR V2 - El guardaespaldas de Pomposo 🛡️
Sistema de protección de código con clasificación inteligente de errores.

Pipeline de seguridad:
1. Backup → 2. Escritura Staging → 3. Validación Sintaxis → 4. Aplicación → 5. (Si falla) Restauración

Nuevo en V2:
- ErrorClassifier: clasifica errores en AUTO_FIX / SUGGEST_FIX / NOTIFY_ONLY
- Validación profunda con subprocess aislado
- Historial de cambios
"""

import os
import sys
import re
import shutil
import py_compile
import ast
import traceback
import subprocess
from enum import Enum
from datetime import datetime
from pathlib import Path

# Directorios de trabajo
BACKUP_DIR = Path(__file__).parent / "backups"
STAGING_DIR = Path(__file__).parent / "staging"
COMMANDS_DIR = Path(__file__).parent / "commands"
PROJECT_ROOT = Path(__file__).parent

# Asegurar que existan las carpetas
BACKUP_DIR.mkdir(exist_ok=True)
STAGING_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════
#  CLASIFICADOR DE ERRORES
# ═══════════════════════════════════════════

class ErrorSeverity(Enum):
    """Niveles de severidad para errores detectados."""
    AUTO_FIX = "auto_fix"          # Se repara solo, sin preguntar
    SUGGEST_FIX = "suggest_fix"    # Se genera parche, pero se pide confirmación
    NOTIFY_ONLY = "notify_only"    # Solo se notifica al dueño, no se puede reparar


class ErrorClassifier:
    """
    Clasifica errores automáticamente para decidir el curso de acción.
    Analiza el tipo de excepción, el traceback y patrones conocidos.
    """
    
    # Patrones de errores que se pueden reparar automáticamente
    AUTO_FIX_PATTERNS = {
        # Errores de import
        "ModuleNotFoundError": ErrorSeverity.AUTO_FIX,
        "ImportError": ErrorSeverity.AUTO_FIX,
        # Errores de nombre (variable/función no definida)
        "NameError": ErrorSeverity.SUGGEST_FIX,
        # Errores de atributo
        "AttributeError": ErrorSeverity.SUGGEST_FIX,
        # Errores de sintaxis
        "SyntaxError": ErrorSeverity.AUTO_FIX,
        "IndentationError": ErrorSeverity.AUTO_FIX,
        # Errores de tipo
        "TypeError": ErrorSeverity.SUGGEST_FIX,
        # Errores de índice/clave
        "KeyError": ErrorSeverity.SUGGEST_FIX,
        "IndexError": ErrorSeverity.SUGGEST_FIX,
    }
    
    # Errores que NUNCA se deben intentar reparar automáticamente
    NOTIFY_ONLY_PATTERNS = [
        "ConnectionError",
        "TimeoutError",
        "aiohttp.ClientError",
        "discord.errors.Forbidden",
        "discord.errors.HTTPException",
        "RateLimited",
        "InvalidToken",
        "LoginFailure",
        "PermissionError",
    ]
    
    # Palabras clave en tracebacks que indican errores de API/red
    EXTERNAL_ERROR_KEYWORDS = [
        "api", "rate_limit", "rate limit", "quota", "forbidden",
        "unauthorized", "timeout", "connection", "ssl", "dns",
        "429", "403", "401", "500", "502", "503",
    ]
    
    @classmethod
    def classify(cls, error: Exception, traceback_str: str = "") -> ErrorSeverity:
        """
        Clasifica un error y retorna su severidad.
        
        Args:
            error: La excepción capturada
            traceback_str: El traceback completo como string
            
        Returns:
            ErrorSeverity indicando cómo se debe manejar
        """
        error_type = type(error).__name__
        error_str = str(error).lower()
        tb_lower = traceback_str.lower()
        
        # 1. Verificar si es un error externo/irrecuperable
        for pattern in cls.NOTIFY_ONLY_PATTERNS:
            if pattern.lower() in error_type.lower() or pattern.lower() in error_str:
                return ErrorSeverity.NOTIFY_ONLY
        
        # 2. Verificar palabras clave de errores externos
        for keyword in cls.EXTERNAL_ERROR_KEYWORDS:
            if keyword in error_str or keyword in tb_lower:
                return ErrorSeverity.NOTIFY_ONLY
        
        # 3. Si el error viene de librerías externas (no de commands/), no reparar
        if traceback_str:
            # Buscar si el error se origina en nuestro código
            our_code = any(
                marker in traceback_str 
                for marker in ["commands/", "commands\\", "safe_editor", "main.py"]
            )
            if not our_code:
                return ErrorSeverity.NOTIFY_ONLY
        
        # 4. Clasificar por tipo de error
        if error_type in cls.AUTO_FIX_PATTERNS:
            severity = cls.AUTO_FIX_PATTERNS[error_type]
            
            # Refinar: SyntaxError/IndentationError en nuestro código → AUTO_FIX
            # Pero si es un error muy complejo, subir a SUGGEST_FIX
            if severity == ErrorSeverity.AUTO_FIX:
                # Verificar complejidad del error
                if len(traceback_str.split("\n")) > 20:
                    return ErrorSeverity.SUGGEST_FIX
            
            return severity
        
        # 5. Cualquier otro error desconocido → SUGGEST_FIX por defecto
        return ErrorSeverity.SUGGEST_FIX
    
    @classmethod
    def get_severity_emoji(cls, severity: ErrorSeverity) -> str:
        """Retorna el emoji apropiado para cada nivel de severidad."""
        return {
            ErrorSeverity.AUTO_FIX: "🔧",
            ErrorSeverity.SUGGEST_FIX: "💡",
            ErrorSeverity.NOTIFY_ONLY: "🚨",
        }.get(severity, "❓")
    
    @classmethod
    def get_severity_label(cls, severity: ErrorSeverity) -> str:
        """Retorna etiqueta legible para cada severidad."""
        return {
            ErrorSeverity.AUTO_FIX: "Reparación Automática",
            ErrorSeverity.SUGGEST_FIX: "Parche Sugerido",
            ErrorSeverity.NOTIFY_ONLY: "Solo Notificación",
        }.get(severity, "Desconocido")


# ═══════════════════════════════════════════
#  SAFE EDITOR V2
# ═══════════════════════════════════════════

class SafeEditor:
    """
    El guardián del código V2. Ahora con clasificación inteligente de errores
    y validación profunda.
    """
    
    MAX_HISTORY = 10  # Máximo de entradas en el historial
    
    def __init__(self):
        self.staging_file = STAGING_DIR / "propuesta.py"
        self.pending_patch = None
        self.pending_target = None
        self.change_history = []  # Historial de cambios recientes
        
    def backup_file(self, target_path: str) -> str:
        """
        Crea un backup del archivo ANTES de modificarlo.
        Retorna la ruta del backup o None si el archivo no existe.
        """
        target = Path(target_path)
        if not target.exists():
            return None
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{target.stem}.bak_{timestamp}{target.suffix}"
        backup_path = BACKUP_DIR / backup_name
        
        shutil.copy2(target, backup_path)
        print(f"📦 Backup creado: {backup_path}")
        return str(backup_path)
    
    def write_staged_code(self, code: str, filename: str = "propuesta.py") -> str:
        """
        Escribe código propuesto en staging para revisión.
        """
        staged_path = STAGING_DIR / filename
        with open(staged_path, 'w', encoding='utf-8') as f:
            f.write(code)
        print(f"📝 Código guardado en staging: {staged_path}")
        return str(staged_path)
    
    def validate_syntax(self, file_path: str) -> tuple:
        """
        Valida la sintaxis del código SIN ejecutarlo.
        
        Retorna: (es_valido: bool, error_mensaje: str o None)
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source)
            py_compile.compile(file_path, doraise=True)
            return True, None
            
        except SyntaxError as e:
            error_msg = f"Línea {e.lineno}: {e.msg}\n```\n{e.text}```" if e.text else str(e)
            return False, f"🚫 Error de sintaxis: {error_msg}"
            
        except py_compile.PyCompileError as e:
            return False, f"🚫 Error de compilación: {e}"
            
        except Exception as e:
            return False, f"🚫 Error inesperado: {e}"
    
    def validate_deep(self, file_path: str) -> tuple:
        """
        Validación profunda: ejecuta un import del módulo en un subprocess aislado
        para detectar errores de import y errores de inicialización
        que validate_syntax no puede capturar.
        
        Retorna: (es_valido: bool, error_mensaje: str o None)
        """
        # Primero validar sintaxis básica
        is_valid, error = self.validate_syntax(file_path)
        if not is_valid:
            return False, error
        
        try:
            # Ejecutar un import del archivo en un proceso aislado
            result = subprocess.run(
                [sys.executable, "-c", f"import ast; ast.parse(open(r'{file_path}', encoding='utf-8').read()); print('OK')"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(PROJECT_ROOT)
            )
            
            if result.returncode != 0:
                return False, f"🚫 Error en validación profunda:\n```\n{result.stderr[:500]}```"
            
            return True, None
            
        except subprocess.TimeoutExpired:
            return False, "🚫 La validación tardó demasiado (posible loop infinito)"
        except Exception as e:
            # Si el subprocess falla, al menos la sintaxis estaba bien
            return True, None
    
    def apply_code(self, target_path: str, source_path: str = None, auto_fix: bool = False) -> tuple:
        """
        Aplica el código de staging al archivo objetivo.
        Pipeline: Validar → Backup → Copiar
        
        Args:
            target_path: Ruta del archivo destino
            source_path: Ruta del código fuente (default: staging)
            auto_fix: Si es True, se registra como reparación automática
            
        Retorna: (exito: bool, mensaje: str)
        """
        source = Path(source_path) if source_path else self.staging_file
        target = Path(target_path)
        
        if not source.exists():
            return False, "❌ No hay código en staging."
        
        # Paso 1: Validar sintaxis
        is_valid, error = self.validate_deep(str(source))
        if not is_valid:
            return False, f"El código no pasó la validación:\n{error}"
        
        # Paso 2: Backup del archivo existente
        backup_path = None
        if target.exists():
            backup_path = self.backup_file(str(target))
            if not backup_path:
                return False, "❌ No se pudo crear backup. Abortando."
        
        # Paso 3: Copiar código validado
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            
            # Registrar en historial
            action = "auto-fix" if auto_fix else "manual"
            self._add_to_history(action, str(target), backup_path)
            
            return True, f"✅ Código aplicado exitosamente a `{target.name}`"
            
        except Exception as e:
            # Intentar restaurar si algo falla
            if backup_path and target.exists():
                self.restore_latest_backup(str(target))
            return False, f"❌ Error al aplicar código: {e}"
    
    def restore_latest_backup(self, target_path: str) -> tuple:
        """Restaura el backup más reciente de un archivo."""
        target = Path(target_path)
        stem = target.stem
        
        backups = list(BACKUP_DIR.glob(f"{stem}.bak_*{target.suffix}"))
        
        if not backups:
            return False, f"❌ No hay backups para `{target.name}`."
        
        latest = max(backups, key=lambda p: p.stat().st_mtime)
        
        try:
            shutil.copy2(latest, target)
            return True, f"✅ Restaurado desde `{latest.name}`"
        except Exception as e:
            return False, f"❌ Error al restaurar: {e}"
    
    def list_backups(self, filename: str = None) -> list:
        """Lista todos los backups disponibles."""
        pattern = f"{filename}.bak_*" if filename else "*.bak_*"
        backups = list(BACKUP_DIR.glob(pattern))
        
        result = []
        for b in sorted(backups, key=lambda p: p.stat().st_mtime, reverse=True):
            mtime = datetime.fromtimestamp(b.stat().st_mtime)
            result.append({
                'path': str(b),
                'name': b.name,
                'date': mtime.strftime("%Y-%m-%d %H:%M:%S"),
                'size': b.stat().st_size
            })
        return result
    
    def get_staged_code(self) -> str:
        """Retorna el código actualmente en staging."""
        if self.staging_file.exists():
            with open(self.staging_file, 'r', encoding='utf-8') as f:
                return f.read()
        return None
    
    def clear_staging(self):
        """Limpia el área de staging."""
        if self.staging_file.exists():
            self.staging_file.unlink()
            
    def set_pending_patch(self, code: str, target_path: str):
        """Guarda un parche pendiente de auto-reparación."""
        self.pending_patch = code
        self.pending_target = target_path
        self.write_staged_code(code, "parche_autorepair.py")
        
    def get_pending_patch(self) -> tuple:
        """Retorna el parche pendiente (código, target) o (None, None)."""
        return self.pending_patch, self.pending_target
    
    def clear_pending_patch(self):
        """Limpia el parche pendiente."""
        self.pending_patch = None
        self.pending_target = None
        patch_file = STAGING_DIR / "parche_autorepair.py"
        if patch_file.exists():
            patch_file.unlink()
    
    # ═══ Historial de Cambios ═══
    
    def _add_to_history(self, action: str, target: str, backup: str = None):
        """Registra una acción en el historial."""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "file": Path(target).name,
            "backup": Path(backup).name if backup else None,
        }
        self.change_history.append(entry)
        # Mantener solo las últimas N entradas
        if len(self.change_history) > self.MAX_HISTORY:
            self.change_history = self.change_history[-self.MAX_HISTORY:]
    
    def get_history(self) -> list:
        """Retorna el historial de cambios recientes."""
        return list(reversed(self.change_history))
    
    # ═══ Utilidades ═══
    
    @staticmethod
    def extract_code_from_markdown(text: str) -> str:
        """Extrae código Python de un bloque de Markdown."""
        # Buscar bloque de código Python
        pattern = r"```(?:python)?\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        
        if matches:
            return matches[0].strip()
        
        # Si no hay bloques, asumir que todo es código
        return text.strip()
    
    @staticmethod
    def read_file_safe(file_path: str) -> str:
        """Lee un archivo de forma segura, retorna None si falla."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return None


# Instancia global para uso en los cogs
safe_editor = SafeEditor()
