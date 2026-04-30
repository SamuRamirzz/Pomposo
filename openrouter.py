"""
openrouter.py — Cliente de IA para Pomposo
Modelos:
  - Texto: inclusionai/ling-2.6-1t:free
  - Visión: meta-llama/llama-3.2-11b-vision-instruct:free
  - Clasificación ligera: minimax/minimax-m2.5:free (con fallback)
Incluye retry automático en 429 con backoff.
"""

import os
import aiohttp
import asyncio
import logging

OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Modelo principal de texto
MODEL_TEXT = "inclusionai/ling-2.6-1t:free"
# Modelo de visión (imágenes y GIFs)
MODEL_VISION = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
# Modelo ligero para clasificación (me_estan_hablando, decidir_accion)
MODEL_LIGHT = "google/gemma-3-12b-it:free"
# Fallback si el modelo principal falla
MODEL_FALLBACK = "google/gemma-4-31b-it:free"

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


async def _call_api(
    session: aiohttp.ClientSession,
    model: str,
    system_prompt: str,
    messages: list,
    temperature: float,
    max_tokens: int,
) -> tuple[str | None, int]:
    """
    Hace una sola llamada a la API.
    Retorna (texto_respuesta, status_code).
    Si hay error, retorna (None, status_code).
    """
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

    async with session.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers=headers,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=60)
    ) as resp:
        if resp.status == 200:
            data = await resp.json()
            try:
                return data['choices'][0]['message']['content'], 200
            except (KeyError, IndexError):
                logger.error(f"Respuesta inesperada: {data}")
                return None, 200

        error_text = await resp.text()
        logger.error(f"OpenRouter error {resp.status} (modelo: {model}): {error_text[:200]}")
        return None, resp.status


async def chat_completion(
    system_prompt: str,
    messages: list,
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: dict = None  # ignorado, se maneja via prompt
) -> str:
    """
    Llama a OpenRouter con retry automático en 429.
    Si el modelo principal falla por rate limit, intenta con fallback.
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("Falta OPENROUTER_API_KEY en el .env")

    # Seleccionar modelo
    if model is None:
        model = MODEL_VISION if _has_images(messages) else MODEL_TEXT

    logger.info(f"OpenRouter modelo: {model}")

    async with aiohttp.ClientSession() as session:
        # Intentar con el modelo principal una vez
        result, status = await _call_api(
            session, model, system_prompt, messages, temperature, max_tokens
        )

        if status == 200 and result:
            return result

        if status == 429:
            # Intentar con fallback si falla por rate limit
            if model != MODEL_FALLBACK:
                logger.warning(f"Rate limit en {model}, usando fallback: {MODEL_FALLBACK}")
                result, status = await _call_api(
                    session, MODEL_FALLBACK, system_prompt, messages, temperature, max_tokens
                )
                if status == 200 and result:
                    return result
            logger.error(f"Rate limit persistente en {model} y fallback.")
            return None # En lugar de lanzar excepcion, retornamos None para que avise al usuario suavemente

        if status == 401:
            logger.error("OPENROUTER_API_KEY inválida o expirada")
            return None
        if status == 400:
            logger.error(f"Request inválida para el modelo {model}")
            return None
        if status == 500:
            logger.error("Servidor de OpenRouter en mantenimiento")
            return None
        
        logger.error(f"OpenRouter HTTP {status}")
        return None