import os
import aiohttp
import logging

GROQ_KEY = os.getenv('GROQ_API_KEY', '')

MODEL_TEXT   = "llama-3.3-70b-versatile"       # Solo texto — más capaz y generoso
MODEL_VISION = "llama-3.2-11b-vision-preview"  # Texto + imágenes


def _has_images(messages: list) -> bool:
    """Detecta si algún mensaje contiene imágenes en base64 o URL."""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            if any(block.get("type") == "image_url" for block in content):
                return True
    return False


async def chat_completion(
    system_prompt: str,
    messages: list,
    model: str = None,          # None = selección automática
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: dict = None
) -> str:
    if not GROQ_KEY:
        raise ValueError("Falta GROQ_API_KEY en el .env")

    # Selección automática de modelo si no se fuerza uno
    if model is None:
        model = MODEL_VISION if _has_images(messages) else MODEL_TEXT

    logging.info(f"Groq modelo seleccionado: {model}")

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=45)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['choices'][0]['message']['content']

            error = await resp.text()
            logging.error(f"Groq error {resp.status} (modelo: {model}): {error}")
            raise Exception(f"Groq devolvió HTTP {resp.status}")