import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

# --- Carga de Secretos ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# --- Configuración del Bot (Sin comandos, solo para DMs) ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix='¿',
    intents=intents,
    help_command=None
)


# --- Evento on_ready ---
@bot.event
async def on_ready():
    print(f"✅ Conectado como {bot.user}")
    print("=" * 60)
    print("TERMINAL INTERACTIVA - ENVÍO DE DMs")
    print("=" * 60)
    print("\nComandos disponibles:")
    print("  dm <usuario_id> <mensaje>  - Enviar DM a un usuario")
    print("  canal <canal_id> <mensaje> - Enviar mensaje a un canal")
    print("  salir                      - Cerrar el bot")
    print("=" * 60)
    
    # Iniciar el loop de la terminal
    asyncio.create_task(terminal_loop())


# --- Evento on_message (solo para ver DMs recibidos) ---
@bot.event
async def on_message(message):
    if isinstance(message.channel, discord.DMChannel) and message.author != bot.user:
        print(f"\n[📩 DM RECIBIDO de {message.author.name}]: {message.content}")
        
        if message.attachments:
            print("   [📸 Archivos adjuntos]:")
            for attachment in message.attachments:
                print(f"   🔗 {attachment.url}")
        
        print("-" * 60)


# --- Terminal Interactiva ---
async def terminal_loop():
    """Loop que lee comandos desde la terminal."""
    await asyncio.sleep(2)  # Esperar a que el bot esté completamente listo
    
    while True:
        try:
            # Leer input de forma asíncrona
            comando = await asyncio.to_thread(input, "\n> ")
            comando = comando.strip()
            
            if not comando:
                continue
            
            partes = comando.split(' ', 2)
            accion = partes[0].lower()
            
            # Comando: salir
            if accion == "salir":
                print("👋 Cerrando bot...")
                await bot.close()
                break
            
            # Comando: dm <user_id> <mensaje>
            elif accion == "dm" and len(partes) >= 3:
                user_id = partes[1]
                mensaje = partes[2]
                
                try:
                    user_id = int(user_id)
                    user = await bot.fetch_user(user_id)
                    await user.send(mensaje)
                    print(f"✅ DM enviado a {user.name}")
                except ValueError:
                    print("❌ Error: El ID del usuario debe ser un número")
                except discord.NotFound:
                    print(f"❌ Error: No se encontró el usuario con ID {user_id}")
                except discord.Forbidden:
                    print(f"❌ Error: No puedo enviar DMs a {user.name} (tiene los DMs cerrados)")
                except Exception as e:
                    print(f"❌ Error: {e}")
            
            # Comando: canal <channel_id> <mensaje>
            elif accion == "canal" and len(partes) >= 3:
                channel_id = partes[1]
                mensaje = partes[2]
                
                try:
                    channel_id = int(channel_id)
                    channel = bot.get_channel(channel_id)
                    
                    if not channel:
                        channel = await bot.fetch_channel(channel_id)
                    
                    await channel.send(mensaje)
                    print(f"✅ Mensaje enviado al canal #{channel.name}")
                except ValueError:
                    print("❌ Error: El ID del canal debe ser un número")
                except discord.NotFound:
                    print(f"❌ Error: No se encontró el canal con ID {channel_id}")
                except discord.Forbidden:
                    print(f"❌ Error: No tengo permisos para escribir en ese canal")
                except Exception as e:
                    print(f"❌ Error: {e}")
            
            else:
                print("❌ Comando no reconocido. Usa:")
                print("   dm <usuario_id> <mensaje>")
                print("   canal <canal_id> <mensaje>")
                print("   salir")
        
        except Exception as e:
            print(f"⚠️ Error en terminal: {e}")


# --- Iniciar el bot ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: No se encontró DISCORD_TOKEN en el archivo .env")
    else:
        bot.run(DISCORD_TOKEN)
