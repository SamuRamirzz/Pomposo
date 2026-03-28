import os
import aiohttp
import logging

# Tu API Key de OpenRouter
OR_KEY = os.getenv('OPENROUTER_API_KEY', '')

# Modelos recomendados gratuitos
# - iaminimax/minimax-m2.5:free (Elegido por el usuario)
# - google/gemini-2.0-flash-lite-preview-02-05:free
DEFAULT_MODEL = "iaminimax/minimax-m2.5:free"

async def chat_completion(
    system_prompt: str,
    messages: list,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: dict = None
) -> str:
    """
    Envía una petición asíncrona a OpenRouter mediante aiohttp.
    
    :param system_prompt: El prompt de sistema con instrucciones y personalidad.
    :param messages: Historial de la conversación en formato [{'role': 'user/assistant', 'content': '...'}].
    :param model: El modelo de IA a usar.
    :param temperature: Creatividad.
    :param max_tokens: Límite de la respuesta.
    :param response_format: Formato de respuesta dict (ej: {"type": "json_object"}).
    :return: La respuesta de la IA en formato texto puro.
    """
    if not OR_KEY:
        raise ValueError(" Faltan credenciales de OpenRouter. Configura OPENROUTER_API_KEY en tu .env")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OR_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/SamuelBot", # Requisito OpenRouter
        "X-Title": "Pomposo Discord Bot"
    }
    
    # Preparar el array de mensajes
    # OpenRouter y OpenAI esperan que el system prompt sea el primer mensaje
    formatted_messages = [{"role": "system", "content": system_prompt}] + messages
    
    payload = {
        "model": model,
        "messages": formatted_messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    if response_format:
        payload["response_format"] = response_format
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content']
                else:
                    error_text = await resp.text()
                    logging.error(f"Error de OpenRouter ({resp.status}): {error_text}")
                    raise Exception(f"OpenRouter API devolvió error HTTP {resp.status}")
    except Exception as e:
        logging.error(f"Falla en comunicación con OpenRouter: {e}")
        raise
