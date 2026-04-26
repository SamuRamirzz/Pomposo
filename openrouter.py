"""
openrouter.py — Cliente de IA para Pomposo
Ahora usa OpenRouter con MiniMax M2.5 (gratis, sin censura, sin límites)
"""

import os
import aiohttp
import logging

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

MODEL_TEXT = "minimax/minimax-text-01"
MODEL_VISION = "minimax/minimax-text-01"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openrouter")


def _has_images(messages: list) -> bool:
    """Detecta si algún mensaje contiene imágenes."""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            if any(block.get("type") == "image_url" for block in content):
                return True
    return False


async def chat_completion(
    system_prompt: str,
    messages: list,
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: dict = None
) -> str:
    """
    Llama a OpenRouter con MiniMax M2.5.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError(
            "Falta OPENROUTER_API_KEY en el .env\n"
            "Consíguela en https://openrouter.ai (gratis)"
        )

    if model is None:
        model = MODEL_VISION if _has_images(messages) else MODEL_TEXT

    logger.info(f"OpenRouter modelo: {model}")

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://discord.com",
        "X-Title": "Pomposo Bot",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                try:
                    return data['choices'][0]['message']['content']
                except (KeyError, IndexError) as e:
                    logger.error(f"Respuesta inesperada: {data}")
                    raise Exception(f"No se pudo parsear respuesta: {e}")

            error = await resp.text()
            logger.error(f"OpenRouter error {resp.status}: {error[:300]}")

            if resp.status == 401:
                raise Exception("OPENROUTER_API_KEY inválida o expirada")
            elif resp.status == 429:
                raise Exception("Límite de rate limit. Espera un momento.")
            elif resp.status == 500:
                raise Exception("Servidor de OpenRouter en mantenimiento")
            else:
                raise Exception(f"OpenRouter HTTP {resp.status}")