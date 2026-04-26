# pomposo_brain.py

import os
import json
import logging
import asyncio
from openrouter import chat_completion

class PomposoBrain:
    def __init__(self, memory_file='bot_memory.txt'):
        self.memory_file = memory_file

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
        
        IMPORTANTE: TU ÚNICA SALIDA DEBE SER EL JSON CRUDO. NO USES BLOQUES DE CÓDIGO NI MARKDOWN. SOLO { ... }.
        """

    def _sync_ddg_search(self, query):
        """Realiza una búsqueda en DuckDuckGo (síncrona)."""
        from duckduckgo_search import DDGS
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region='wt-wt', safesearch='moderate', max_results=3))
                if not results:
                    return None
                
                formatted = []
                for item in results:
                    formatted.append(f"- {item.get('title')}: {item.get('body')}")
                return "\n".join(formatted)
        except Exception as e:
            print(f"Error DuckDuckGo: {e}")
            return None

    async def generate_response(self, user_text, image_url=None):
        """
        Procesa el texto del usuario y genera una respuesta 24/7 en formato JSON para dictado Voice TTS.
        """
        user_text_lower = user_text.lower()

        # --- Reacciones Rápidas (Bromas) ---
        if "te partiremos la torta" in user_text_lower or "te celebraremos" in user_text_lower:
            return {"chat": "oye nooo alejate emfermo >:(", "voice": "Oye no, aléjate enfermo."}
        
        # --- Lógica de IA ---
        memory_context = self._load_memory()
        system_prompt = self._get_system_prompt()
        
        search_context = ""
        # Heurístico simple para búsqueda en tiempo real
        if any(w in user_text_lower for w in ['quien', 'quién', 'cuando', 'cuándo', 'noticias', 'precio', 'clima', 'ganó']):
             print(f" Detectado intento de búsqueda para voz: {user_text}")
             search_results = await asyncio.to_thread(self._sync_ddg_search, user_text)
             
             if search_results:
                 search_context = f"\nINFORMACIÓN DE BÚSQUEDA RECIENTE (WEB):\n{search_results}\nUsa esto para responder si es relevante."
             else:
                 search_context = "\n(No se encontraron resultados en búsqueda, si no sabes la respuesta dilo)."

        full_prompt = f"""
        CONTEXTO DE MEMORIA:
        {memory_context}
        
        {search_context}
        
        Si el usuario mandó una imagen, usa esta URL (o indícalo): {image_url if image_url else 'Ninguna'}
        """

        try:
            import json
            # OpenRouter ya sabe qué hacer si enviamos un json string de request
            response_text = await chat_completion(
                system_prompt=system_prompt + full_prompt,
                messages=[{"role": "user", "content": user_text}],
                response_format={"type": "json_object"}
            )
            
            # Limpiar posible markdown en la respuesta
            clean_text = response_text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:-3]
            elif clean_text.startswith("```"):
                clean_text = clean_text[3:-3]
                
            try:
                data = json.loads(clean_text)
                return data # Retorna dict {'chat': ..., 'voice': ...}
            except json.JSONDecodeError:
                return {"chat": clean_text, "voice": clean_text}

        except Exception as e:
            print(f"Error generando respuesta OpenRouter Voice: {e}")
            return {"chat": "me mori (error) jeje", "voice": "Tuve un error interno."}

# Bloque de prueba
if __name__ == "__main__":
    brain = PomposoBrain()
    
    print("--- Test: Saludo ---")
    res1 = asyncio.run(brain.generate_response("Hola pomposo como estas"))
    print(res1)
    
    print("\n--- Test: Broma ---")
    res2 = asyncio.run(brain.generate_response("te partiremos la torta"))
    print(res2)
