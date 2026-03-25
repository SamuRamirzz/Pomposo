import os
import json
import logging
from dotenv import load_dotenv
from google import genai
from google.genai import types
from googleapiclient.discovery import build

load_dotenv()

class PomposoBrain:
    def __init__(self, memory_file='bot_memory.txt'):
        self.memory_file = memory_file
        self.api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.cse_id = os.getenv("GOOGLE_CSE_ID") or os.getenv("GOOGLE_SEARCH_CX_ID")
        self.search_api_key = os.getenv("GOOGLE_SEARCH_API_KEY") # Sometimes separate from GenAI key
        
        # Fallback if only one key is provided
        if not self.search_api_key and self.api_key:
            self.search_api_key = self.api_key

        if self.api_key:
             self.client = genai.Client(api_key=self.api_key)
             # GEMINI 3.1 FLASH LITE
             self.model_name = 'gemini-2.0-flash-lite'
        else:
            print(" ADVERTENCIA: No se encontró GOOGLE_API_KEY. La IA no funcionará correctamente.")
            self.client = None

    def _load_memory(self):
        """Lee el archivo de memoria para dar contexto."""
        if not os.path.exists(self.memory_file):
            return "No hay recuerdos previos."
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                return f.read()[-2000:] # Últimos 2000 caracteres de contexto
        except Exception as e:
            print(f"Error leyendo memoria: {e}")
            return ""

    def _get_system_prompt(self):
        """Genera el System Prompt con la personalidad y reglas."""
        return """
        Eres Pomposo. Tu personalidad escrita es caótica y con mala ortografía (ej: ola soi um pomposito jeje).
        Sin embargo, tienes una voz masculina que debe sonar clara.
        
        FORMATO DE RESPUESTA (JSON):
        Debes responder SIEMPRE con un objeto JSON válido con dos campos:
        1. "chat": Tu respuesta escrita con tu personalidad (faltas de ortografía, "sorra", "gei", etc).
        2. "voice": La MISMA respuesta pero corregida gramaticalmente para que el TTS la pronuncie bien.
        
        Ejemplo:
        {
          "chat": "ola k ase sorra, me das asco jeje",
          "voice": "Hola, ¿qué haces zorra? Me das asco, jeje."
        }

        REGLAS DE AMIGOS:
        - NO menciones a Drake, Reb, Pancho, Sany o Crawler a menos que el usuario use las palabras clave.
        
        SI NO SABES ALGO:
        - Di 'no lo sé jeje' (y su versión corregida en 'voice').
        """

    def _google_search(self, query):
        """Realiza una búsqueda en Google usando Custom Search JSON API."""
        if not self.search_api_key or not self.cse_id:
            return None
        
        try:
            service = build("customsearch", "v1", developerKey=self.search_api_key)
            res = service.cse().list(q=query, cx=self.cse_id, num=3).execute()
            
            if 'items' not in res:
                return None
                
            results = []
            for item in res['items']:
                title = item.get('title')
                snippet = item.get('snippet')
                results.append(f"- {title}: {snippet}")
            
            return "\n".join(results)
        except Exception as e:
            print(f"Error en Google Search: {e}")
            return None

    async def generate_response(self, user_text, image_url=None):
        """
        Procesa el texto del usuario y genera una respuesta.
        1. Detecta keywords y reacciones rápidas.
        2. Busca en Google si es necesario.
        3. Genera respuesta con LLM.
        """
        user_text_lower = user_text.lower()

        # --- Reacciones Rápidas (Bromas) ---
        if "te partiremos la torta" in user_text_lower or "te celebraremos" in user_text_lower:
            return "oye nooo alejate emfermo >:("
        
        # --- Lógica de IA ---
        if not self.client:
            return "no tengo cerebro ahorita (falta api key) jeje"

        memory_context = self._load_memory()
        system_prompt = self._get_system_prompt()
        
        # Prompt inicial para decidir si buscar
        # Nota: En una implementación más avanzada, usaríamos function calling.
        # Aquí usaremos un heurístico simple o instrucción directa al modelo si el usuario pregunta algo fáctico.
        
        search_context = ""
        # Heurístico simple para búsqueda: preguntas de 'quién', 'cuándo', 'qué pasó', 'noticias'
        if any(w in user_text_lower for w in ['quien', 'quién', 'cuando', 'cuándo', 'noticias', 'precio', 'clima', 'ganó']):
             print(f" Detectado intento de búsqueda para: {user_text}")
             search_results = self._google_search(user_text)
             if search_results:
                 search_context = f"\nINFORMACIÓN DE BÚSQUEDA RECIENTE (Google):\n{search_results}\nUsa esto para responder si es relevante."
             else:
                 search_context = "\n(No se encontraron resultados en Google, si no sabes la respuesta dilo)."

        full_prompt = f"""
        {system_prompt}
        
        CONTEXTO DE MEMORIA (Conversaciones pasadas):
        {memory_context}
        
        {search_context}
        
        USUARIO: {user_text}
        POMPOSO:
        """

        try:
            # Soporte para imágenes (Visión)
            # Nota: El nuevo SDK maneja imágenes distinto, aquí asumimos texto por ahora para simplificar la migración
            # Si llega image_url, la añadimos al texto (idealmente descargaríamos y pasaríamos bytes)
            if image_url:
                 full_prompt += f"\n(El usuario también envió esta imagen: {image_url})"

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            # Intentar parsear JSON
            try:
                data = json.loads(response.text)
                return data # Retorna dict {'chat': ..., 'voice': ...}
            except json.JSONDecodeError:
                # Fallback si el modelo falla el JSON
                text = response.text
                return {"chat": text, "voice": text}

        except Exception as inner_e:
            print(f"Error generando contenido: {inner_e}")
            return {"chat": "me mori (error interno) jeje", "voice": "Tuve un error interno."}

        except Exception as e:
            print(f"Error generando respuesta LLM: {e}")
            return {"chat": "me mori (error) jeje", "voice": "Tuve un error interno."}

# Bloque de prueba
if __name__ == "__main__":
    import asyncio
    brain = PomposoBrain()
    
    # Simular interacción
    print("--- Test: Saludo ---")
    print(asyncio.run(brain.generate_response("Hola pomposo como estas")))
    
    print("\n--- Test: Broma ---")
    print(asyncio.run(brain.generate_response("te partiremos la torta")))
