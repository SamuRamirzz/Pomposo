# 🎩 Pomposo — Bot de Discord con IA

Bot de Discord con personalidad propia, IA conversacional (Gemini), sistema de auto-reparación de código, y comandos creativos.

## ✨ Características

- **IA Conversacional** — Chat con personalidad usando Google Gemini, con soporte de imágenes y búsqueda en Google
- **Auto-Reparación Inteligente** — Detecta errores en comandos y los clasifica automáticamente:
  - 🔧 **AUTO_FIX**: Repara automáticamente errores triviales (imports, sintaxis)
  - 💡 **SUGGEST_FIX**: Genera parche y pide confirmación para errores complejos
  - 🚨 **NOTIFY_ONLY**: Notifica al dueño para errores externos irrecuperables
- **Creación de Comandos con IA** — Crea y edita comandos de Discord usando lenguaje natural
- **Sistema de Backups** — Backup automático antes de cualquier cambio de código
- **Fuzzy Matching** — Búsqueda inteligente de usuarios por nombre aproximado

## 📁 Estructura del Proyecto

```
Pomposo/
├── main.py                  # Bot principal, eventos, manejo de errores
├── pomposo_brain.py         # Motor de IA (Gemini) para chat
├── safe_editor.py           # Sistema de protección de código + ErrorClassifier
├── requirements.txt         # Dependencias
├── .env                     # Claves API (NO incluido en el repo)
├── commands/                # Cogs (módulos de comandos)
│   ├── architect.py         # 🏗️ Arquitecto V3 - Auto-reparación y creación de código
│   ├── ask.py               # IA conversacional
│   ├── img.py               # Generación de imágenes
│   ├── agenda.py            # Sistema de agenda/tareas
│   ├── buscador.py          # Búsqueda web
│   ├── deal.py              # Ofertas de videojuegos
│   ├── gatos.py             # Imágenes de gatos
│   ├── inf.py               # Información de usuarios/servidor
│   ├── nick.py              # Gestión de apodos
│   ├── nsfw.py              # Contenido NSFW (restringido)
│   ├── nuke.py              # Limpieza de mensajes
│   ├── punch.py             # Interacciones sociales
│   ├── tocar.py             # Comandos de entretenimiento
│   └── voice_chat.py        # Chat de voz con TTS
├── backups/                 # Backups automáticos de código
├── staging/                 # Código en revisión antes de aplicar
└── Pomposo-Terminal/        # Versión de terminal (experimental)
```

## ⚙️ Configuración

### 1. Clonar el repositorio
```bash
git clone https://github.com/SamuRamirzz/Pomposo.git
cd Pomposo
```

### 2. Crear entorno virtual e instalar dependencias
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 3. Configurar variables de entorno
Crea un archivo `.env` en la raíz del proyecto:
```env
DISCORD_TOKEN=tu_token_de_discord
GEMINI_API_KEY=tu_api_key_de_gemini
GOOGLE_SEARCH_API_KEY=tu_api_key_de_search
GOOGLE_SEARCH_CX_ID=tu_cx_id
```

### 4. Ejecutar
```bash
python main.py
```

## 🏗️ Sistema Arquitecto V3

El Arquitecto es el sistema central de auto-mantenimiento del bot.

### Flujo de Auto-Reparación
```
Error detectado en un comando
          ↓
  ErrorClassifier.classify()
          ↓
  ┌───────┼───────────┐
  ↓       ↓           ↓
AUTO_FIX  SUGGEST_FIX  NOTIFY_ONLY
  ↓       ↓           ↓
Repara    Genera      Solo notifica
auto.     parche →    al dueño
          DM al       (sin parche)
          dueño
```

### Comandos del Arquitecto

| Comando | Descripción |
|---|---|
| `¿nuevo <instrucción>` | Crea un comando nuevo |
| `¿editar <archivo.py> <cambios>` | Edita un archivo existente |
| `¿ok` | Aplica código pendiente |
| `¿ver` | Ver código en staging |
| `¿no` | Descartar código |
| `¿parches` | Ver parches pendientes |
| `¿fix [id]` | Aplicar un parche |
| `¿explica [id]` | Explicar un error |
| `¿undo <archivo>` | Restaurar desde backup |
| `¿historial` | Ver historial de cambios |
| `¿arquitecto` | Guía completa |

## 📝 Changelog

### V3 — 2026-03-22
- **Sistema de auto-reparación inteligente** con clasificación de errores (AUTO_FIX / SUGGEST_FIX / NOTIFY_ONLY)
- **Interfaz de comandos rediseñada**: `¿nuevo` para crear, `¿editar` para modificar
- **Validación profunda** de código con subprocess aislado
- **Rate-limiting** anti-spam para errores repetidos
- **Historial de cambios** visible con `¿historial`
- **Comando de ayuda** `¿arquitecto` con guía completa embebida
- **Embeds mejorados** en mensajes de error y previews de código
- **Seguridad**: `.gitignore` para proteger secretos, credenciales removidas del historial
- **README.md** con documentación completa del proyecto

## 📄 Licencia

Proyecto personal de SamuRamirzz.
