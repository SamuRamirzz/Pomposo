# Pomposo Terminal - Versión Simplificada

Esta es una versión simplificada del bot Pomposo diseñada **solo para uso local** desde la terminal. No tiene comandos de Discord, solo funcionalidad para enviar DMs y mensajes a canales de forma interactiva.

## ¿Por qué existe esto?

Cuando el bot principal está corriendo en la VM (nube), no tienes acceso directo a la terminal para enviar DMs. Esta versión te permite:
- Ejecutar el bot localmente en tu PC
- Enviar DMs desde la terminal
- Ver DMs que recibes en tiempo real
- No interfiere con el bot principal en la VM

## Instalación

### 1. Copiar el token

Copia tu archivo `.env` del proyecto principal:

```powershell
copy ..\\.env .env
```

O crea un archivo `.env` con:
```
DISCORD_TOKEN=tu_token_aqui
```

### 2. Instalar dependencias

```powershell
pip install -r requirements.txt
```

## Uso

### Iniciar el bot

```powershell
python main.py
```

### Comandos disponibles

Una vez iniciado, verás un prompt `>` donde puedes escribir:

#### Enviar DM a un usuario
```
> dm 123456789012345678 Hola, este es un mensaje privado
```

#### Enviar mensaje a un canal
```
> canal 987654321098765432 Hola desde la terminal
```

#### Salir
```
> salir
```

## Obtener IDs

### ID de Usuario
1. En Discord, activa el Modo Desarrollador: `Configuración > Avanzado > Modo Desarrollador`
2. Click derecho en el usuario → `Copiar ID de usuario`

### ID de Canal
1. Click derecho en el canal → `Copiar ID del canal`

## Notas Importantes

- ✅ Puedes ejecutar esto mientras el bot principal está en la VM
- ✅ Ambos usan el mismo token sin problemas
- ✅ Los DMs que recibas aparecerán en la terminal automáticamente
- ⚠️ Esta versión NO tiene comandos de Discord (¿punch, ¿nick, etc.)
- ⚠️ Solo para uso local, no subas esto a la VM
