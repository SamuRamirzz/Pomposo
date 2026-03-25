import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
from google.cloud import texttospeech
import speech_recognition as sr
from discord.ext import voice_recv
from pomposo_brain import PomposoBrain
import time
import io
import scipy.io.wavfile as wav

# --- SINK DE ESCUCHA ---
class PomposoSink(voice_recv.AudioSink):
    def __init__(self, cog, guild_id):
        self.cog = cog
        self.guild_id = guild_id
        self.buffers = {} # {user_id: {'data': bytearray, 'last_packet': time}}
        self.recognizer = sr.Recognizer()

    def wants_opus(self) -> bool:
        return False # Queremos PCM decodificado

    def write(self, user, data):
        if user is None:
            return
            
        now = time.time()
        
        # Inicializar buffer para usuario si no existe
        if user.id not in self.buffers:
            self.buffers[user.id] = {'data': bytearray(), 'last_packet': now}
            print(f" Escuchando a {user.name}...")

        # Append data (stereo 16-bit 48kHz PCM)
        self.buffers[user.id]['data'].extend(data.pcm)
        self.buffers[user.id]['last_packet'] = now
        
        # Lógica de detección de silencio básica manejada por tarea externa
        # (Aquí solo acumulamos)

    def cleanup(self):
        pass

class VoiceChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.brain = PomposoBrain()
        self.active_voice_clients = {} # {guild_id: voice_client}
        self.listening_states = {} # {guild_id: {'state': 'WAITING', 'target_user': None}}
        self.sinks = {} # {guild_id: sink_instance}
        self.check_silence_task = self.bot.loop.create_task(self.check_silence_loop())

    def cog_unload(self):
        self.check_silence_task.cancel()

    async def check_silence_loop(self):
        """Revisa buffers periódicamente para detectar silencios y procesar frases."""
        while True:
            await asyncio.sleep(0.1) # Revisar más rápido (100ms)
            now = time.time()
            
            for guild_id, sink in list(self.sinks.items()):
                for user_id, buffer_data in list(sink.buffers.items()):
                    last_pkt = buffer_data['last_packet']
                    
                    # Si hubo silencio por más de 0.6 segundos, procesamos
                    # (Reducido de 1.2s para mayor velocidad de respuesta)
                    if now - last_pkt > 0.6:
                        audio_data = buffer_data['data']
                        del sink.buffers[user_id] # Limpiar buffer inmediatamente
                        
                        if len(audio_data) > 50000: # Ignorar ruidos cortos (< 0.5s aprox)
                            asyncio.create_task(self.process_audio_buffer(guild_id, user_id, audio_data))

    async def process_audio_buffer(self, guild_id, user_id, pcm_data):
        """Convierte PCM a texto y lo manda al cerebro."""
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        if not user: return

        print(f" Transcribiendo {len(pcm_data)} bytes de {user.name}...")
        
        try:
            # Convertir PCM a WAV en memoria
            # Discord PCM es 48kHz, 2 canales (Stereo), 16-bit
            # SpeechRecognition prefiere Mono, así que tomamos cada 4to byte o promediamos
            # Hack rápido: Usar numpy si estuviera, pero scipy.io.wavfile escribe lo que le des.
            # Convertir bytes a array compatible o guardar como raw.
            # SR necesita 16-bit Mono o Stereo (soporta stereo).
            
            import numpy as np
            # Reconvertir bytearray a numpy array int16
            audio_np = np.frombuffer(pcm_data, dtype=np.int16)
            
            # Reshape a (N, 2) para stereo
            audio_np = audio_np.reshape(-1, 2)
            
            # Convertir a Mono promediando canales (Axis 1)
            audio_mono = audio_np.mean(axis=1).astype(np.int16)
            
            # Escribir a buffer BytesIO como WAV
            wav_buffer = io.BytesIO()
            wav.write(wav_buffer, 48000, audio_mono)
            wav_buffer.seek(0)
            
            # Reconocer
            r = sr.Recognizer()
            with sr.AudioFile(wav_buffer) as source:
                audio = r.record(source) # Leer todo
                
            try:
                # Usar Google Speech Recognition (GRATIS, pero a veces lento)
                text = await self.bot.loop.run_in_executor(None, lambda: r.recognize_google(audio, language="es-ES"))
                print(f" {user.name} dijo: '{text}'")
                
                # ENVIAR AL CEREBRO
                if "pomposo" in text.lower() or "gei" in text.lower() or self.listening_states.get(guild_id, {}).get('state') == 'ACTIVE':
                     await self.handle_brain_interaction(guild_id, user, text, None)
                     
            except sr.UnknownValueError:
                pass # No entendió
            except sr.RequestError as e:
                print(f"Error SR: {e}")
                
        except Exception as e:
             print(f"Error procesando audio: {e}")

    @app_commands.command(name="join", description="Invoca a Pomposo al canal de voz.")
    async def join(self, interaction: discord.Interaction):
        """Conecta el bot al canal de voz y prepara la escucha."""
        if not interaction.user.voice:
            # Personalidad: Respuesta agresiva si no está en VC
            await interaction.response.send_message("sorra metete a un canal primero gei", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        
        # Verificar permisos
        if not channel.permissions_for(interaction.guild.me).connect:
            await interaction.response.send_message("no me dejan entrar a tu club de geis (falta permiso)", ephemeral=True)
            return

        await interaction.response.send_message(f"entrando al {channel.name}... preparen las colas", ephemeral=False)

        try:
            # Desconectar si ya está en otro canal del mismo server
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.disconnect()

            # CONEXIÓN IMPORTANTE: Usar VoiceRecvClient
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
            self.active_voice_clients[interaction.guild.id] = vc
            self.listening_states[interaction.guild.id] = {'state': 'WAITING', 'target_user': None}
            
            # INICIAR ESCUCHA
            sink = PomposoSink(self, interaction.guild.id)
            self.sinks[interaction.guild.id] = sink
            vc.listen(sink)

            print(f" Pomposo conectado y escuchando en {channel.name}")
            
            await interaction.channel.send("ola soi um pomposito jeje (ya te escucho)")

        except Exception as e:
            await interaction.followup.send(f"me mori entrando: {e}")
            print(f"Error joining VC: {e}")

    @app_commands.command(name="leave", description="Echa a Pomposo del canal de voz.")
    async def leave(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            # Despedida Pomposa
            guild_id = interaction.guild.id
            await interaction.response.send_message("fuchi me boy", ephemeral=False)
            await self.speak_tts(guild_id, "fuchi me boy")
            await asyncio.sleep(2) # Esperar a que hable un poco
            
            await interaction.guild.voice_client.disconnect()
            if interaction.guild.id in self.active_voice_clients:
                del self.active_voice_clients[interaction.guild.id]
        else:
            await interaction.response.send_message("ni estoy conectado pendejo", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Maneja la auto-desconexión si el bot se queda solo."""
        if member == self.bot.user:
            return

        # Verificar si el evento ocurrió en el canal donde está el bot
        if member.guild.voice_client:
            bot_channel = member.guild.voice_client.channel
            
            # Si el canal se quedó vacío (solo el bot)
            if bot_channel and len(bot_channel.members) == 1: # 1 porque el bot cuenta como miembro
                print(f" Pomposo saliendo de {bot_channel.name} por inactividad.")
                
                # Enviar despedida al canal de texto asociado (si es posible determinar cuál)
                # O usar TTS antes de salir
                
                # Esperar un momento por si alguien entra rápido (debounce)
                await asyncio.sleep(5)
                # Re-check
                if member.guild.voice_client and member.guild.voice_client.channel and len(member.guild.voice_client.channel.members) == 1:
                    vc = member.guild.voice_client
                    if vc:
                        # Intentar decir algo antes de irse (no bloqueante)
                        # self.speak(vc, "me boy porqe estoi solo sorras") 
                        await vc.disconnect()
                        if member.guild.id in self.active_voice_clients:
                            del self.active_voice_clients[member.guild.id]

    # --- Lógica de Procesamiento de Audio (Simulada/Estructural) ---
    async def process_incoming_audio(self, guild_id, user, text, image_url=None):
        """
        Esta función sería llamada por el callback del STT (Speech-to-Text).
        Maneja la máquina de estados para 'fijar' la atención en un usuario.
        """
        state_data = self.listening_states.get(guild_id)
        if not state_data:
            return

        current_state = state_data['state']
        text_lower = text.lower()

        # 1. ESTADO: ESPERANDO (Escucha pasiva)
        if current_state == 'WAITING':
            # Detectar keywords de activación
            if any(k in text_lower for k in ['pomposo', 'pomposito']):
                print(f" Activado por {user.name}: '{text}'")
                
                # Cambiar estado a ACTIVO para este usuario
                self.listening_states[guild_id]['state'] = 'ACTIVE'
                self.listening_states[guild_id]['target_user'] = user
                
                # Procesar lo que dijo inmediatamente junto con la keyword
                await self.handle_brain_interaction(guild_id, user, text, image_url)
                
                # Reiniciar estado después de responder
                # (Opcional: mantener conversación activa por unos segundos)
                # Por ahora, single-turn
                self.listening_states[guild_id]['state'] = 'WAITING'
                self.listening_states[guild_id]['target_user'] = None

            elif 'gei' in text_lower:
                 # Reacción rápida sin cambio de estado
                 # await self.speak(guild_id, "quien dijo gei? a ver enseñamela")
                 pass

        # 2. ESTADO: ACTIVO (Escuchando a usuario específico)
        elif current_state == 'ACTIVE':
            target = state_data['target_user']
            if user == target:
                # Procesar continuación de la conversación
                await self.handle_brain_interaction(guild_id, user, text, image_url)
                # Volver a waiting (o mantener si implementamos multi-turn timeout)
                self.listening_states[guild_id]['state'] = 'WAITING'
                self.listening_states[guild_id]['target_user'] = None
            else:
                print(f"Ignorando a {user.name} mientras atiendo a {target.name}")

    async def handle_brain_interaction(self, guild_id, user, text, image_url):
        """Envía texto al cerebro y gestiona la respuesta."""
        print(f" Procesando para {user.name}: {text}")
        
        # Generar respuesta (ahora es un dict)
        response_data = await self.brain.generate_response(text, image_url)
        
        # Validar tipo de respuesta
        if isinstance(response_data, dict):
             chat_text = response_data.get('chat', '')
        else:
             chat_text = str(response_data)
        
        # Personalización con nombre del usuario
        if "{user}" in chat_text:
            chat_text = chat_text.replace("{user}", user.display_name)
        
        print(f" Respuesta Chat/Voz (Pomposo Style): {chat_text}")
        
        # Opcional: Escribir en chat también
        # await self.bot.get_channel(channel_id).send(chat_text) 
        
        # Convertir a voz (TTS) - Usando el texto con personalidad (errores incluidos)
        await self.speak_tts(guild_id, chat_text)

    async def speak_tts(self, guild_id, text):
        """
        Genera audio TTS con Google Cloud Text-to-Speech (Neural2).
        Estilo: Pomposito rápido y burlón.
        """
        vc = self.active_voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            return

        print(f" Generando TTS Neural2 (Google): {text}")
        try:
            # Cliente de Google TTS
            # Configuración explícita de credenciales para evitar hang en Windows
            cred_file = "google-credentials.json"
            if os.path.exists(cred_file):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(cred_file)
            
            # Si no existe el archivo y no está la variable, esto fallará rápido en lugar de hang
            # (El hang suele ser por buscar metadata server)
            
            try:
                client = texttospeech.TextToSpeechClient()
            except Exception as e:
                print(f" Error Auth Google: {e}")
                return

            input_text = texttospeech.SynthesisInput(text=text)

            # Configuración de Voz: Neural2 (Hombre Joven)
            voice = texttospeech.VoiceSelectionParams(
                language_code="es-ES",
                name="es-ES-Neural2-B"
                # ssml_gender=texttospeech.SsmlVoiceGender.MALE
            )

            # Configuración de Audio: Personalidad "Pomposita"
            # Pitch +2.0 (más agudo/burlón)
            # Rate 1.15 (rápido)
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                pitch=2.0,
                speaking_rate=1.15
            )

            # Sintetizar
            response = client.synthesize_speech(
                input=input_text, voice=voice, audio_config=audio_config
            )

            # Guardar archivo
            filename = f"tts_{guild_id}.mp3"
            with open(filename, "wb") as out:
                out.write(response.audio_content)
            
            # 2. Reproducir
            if vc.is_playing():
                vc.stop()
                
            # Ruta absoluta a ffmpeg detectada en el entorno del usuario
            ffmpeg_path = r"c:\Users\User\Downloads\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build\bin\ffmpeg.exe"
            
            if not os.path.exists(ffmpeg_path):
                print(f" ERROR CRÍTICO: No se encontró FFmpeg en: {ffmpeg_path}")
                # Intentar fallback al sistema o notificar
            
            source = discord.FFmpegPCMAudio(filename, executable=ffmpeg_path)
            
            def cleanup(error):
                if error:
                    print(f"Error en playback: {error}")
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                        print(" Archivo TTS eliminado.")
                except Exception as e:
                    print(f"Error borrando archivo: {e}")

            vc.play(source, after=cleanup)
            
        except Exception as e:
            print(f" Error en Google Cloud TTS: {e}")
            print(" Verifica que 'google-credentials.json' esté configurado o la variable de entorno GOOGLE_APPLICATION_CREDENTIALS.")

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Maneja mensajes de texto mientras el bot está en VC.
        Actúa como 'oídos' alternativos: si escribes 'pomposo' en el chat, te responde por voz.
        También analiza imágenes.
        """
        if message.author.bot:
            return

        if message.guild and message.guild.id in self.active_voice_clients:
            # --- 1. DETECCIÓN DE TEXTO (Chat-to-Voice) ---
            # Si el usuario escribe algo con "pomposo" o palabras clave, lo procesamos como si lo hubiera dicho.
            content_lower = message.content.lower()
            if "pomposo" in content_lower or "pomposito" in content_lower or "gei" in content_lower:
                print(f" Activado por Texto de {message.author.name}: '{message.content}'")
                await self.process_incoming_audio(
                    message.guild.id,
                    message.author,
                    message.content
                )
                return

            # --- 2. DETECCIÓN DE IMÁGENES (Vision) ---
            if message.attachments:
                # Verificar si es imagen
                image_url = message.attachments[0].url
                if any(ext in image_url.lower() for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                    print(f" Pomposo vio una imagen de {message.author.name}")
                    
                    # Inyectar imagen al flujo de voz
                    await self.process_incoming_audio(
                        message.guild.id, 
                        message.author, 
                        f"mira esta imagen que mande al chat: {image_url}", # Prompt implícito para que la IA sepa que es una imagen
                        image_url=image_url
                    )

async def setup(bot):
    await bot.add_cog(VoiceChat(bot))
