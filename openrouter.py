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
MODEL_VISION = "meta-llama/llama-3.2-11b-vision-instruct:free"
# Modelo ligero para clasificación (me_estan_hablando, decidir_accion)
MODEL_LIGHT = "google/gemma-3-12b-it:free"
# Fallback si el modelo principal tiene rate limit
MODEL_FALLBACK = "google/gemma-3-12b-it:free"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openrouter")

# Máximo de reintentos cuando hay 429
MAX_RETRIES = 3
# Segundos de espera entre reintentos (se multiplica por intento)
RETRY_BASE_DELAY = 2.0


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
        # Intentar con el modelo principal, con reintentos
        for intento in range(MAX_RETRIES):
            result, status = await _call_api(
                session, model, system_prompt, messages, temperature, max_tokens
            )

            if status == 200 and result:
                return result

            if status == 429:
                if intento < MAX_RETRIES - 1:
                    wait = RETRY_BASE_DELAY * (intento + 1)
                    logger.warning(f"Rate limit en {model}, reintentando en {wait}s (intento {intento+1}/{MAX_RETRIES})")
                    await asyncio.sleep(wait)
                    continue
                else:
                    # Agotados los reintentos con el modelo principal
                    # Intentar con fallback si el modelo no era ya el fallback
                    if model != MODEL_FALLBACK:
                        logger.warning(f"Rate limit persistente en {model}, usando fallback: {MODEL_FALLBACK}")
                        result, status = await _call_api(
                            session, MODEL_FALLBACK, system_prompt, messages, temperature, max_tokens
                        )
                        if status == 200 and result:
                            return result
                    raise Exception(f"Rate limit persistente. Intenta en unos segundos.")

            elif status == 401:
                raise Exception("OPENROUTER_API_KEY inválida o expirada")
            elif status == 400:
                raise Exception(f"Request inválida para el modelo {model}")
            elif status == 500:
                raise Exception("Servidor de OpenRouter en mantenimiento")
            else:
                raise Exception(f"OpenRouter HTTP {status}")

    raise Exception("No se pudo obtener respuesta después de todos los reintentos")