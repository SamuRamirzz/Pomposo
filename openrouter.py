"""
openrouter.py  — Ahora usa Google Gemini en vez de Groq.
El nombre del archivo se mantiene igual para no romper los otros comandos
que hagan  `from openrouter import chat_completion`.
"""

import os
import aiohttp
import logging
import json

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Modelos Gemini
MODEL_TEXT   = "gemini-3.1-flash-lite"   # Solo texto — rápido y gratuito
MODEL_VISION = "gemini-3-flash-preview"        # Texto + imágenes (flash-lite no soporta visión)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _has_images(messages: list) -> bool:
    """Detecta si algún mensaje contiene imágenes en base64 o URL."""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            if any(block.get("type") == "image_url" for block in content):
                return True
    return False


def _convert_messages_to_gemini(system_prompt: str, messages: list) -> tuple[str, list]:
    """
    Convierte el historial formato OpenAI a formato Gemini.
    Retorna (system_instruction, contents[])
    """
    contents = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"

        # Contenido simple (string)
        if isinstance(content, str):
            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}]
            })
            continue

        # Contenido mixto (texto + imágenes)
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append({"text": block["text"]})

                elif block.get("type") == "image_url":
                    url_data = block.get("image_url", {}).get("url", "")
                    # Formato data:mime;base64,XXXX
                    if url_data.startswith("data:"):
                        header, b64data = url_data.split(",", 1)
                        mime_type = header.split(":")[1].split(";")[0]
                        parts.append({
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": b64data
                            }
                        })
                    else:
                        # URL externa — la mandamos como fileData
                        parts.append({
                            "fileData": {
                                "mimeType": "image/jpeg",
                                "fileUri": url_data
                            }
                        })
            if parts:
                contents.append({"role": gemini_role, "parts": parts})

    return system_prompt, contents


async def chat_completion(
    system_prompt: str,
    messages: list,
    model: str = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: dict = None   # Se ignora en Gemini (se maneja via prompt)
) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("Falta GEMINI_API_KEY en el .env")

    # Selección automática de modelo
    if model is None:
        model = MODEL_VISION if _has_images(messages) else MODEL_TEXT

    logging.info(f"Gemini modelo seleccionado: {model}")

    system_instruction, contents = _convert_messages_to_gemini(system_prompt, messages)

    payload = {
        "system_instruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
    }

    url = f"{GEMINI_API_BASE}/{model}:generateContent?key={GEMINI_API_KEY}"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=45)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as e:
                    logging.error(f"Gemini respuesta inesperada: {data}")
                    raise Exception(f"No se pudo parsear respuesta de Gemini: {e}")

            error = await resp.text()
            logging.error(f"Gemini error {resp.status} (modelo: {model}): {error}")
            raise Exception(f"Gemini devolvió HTTP {resp.status}: {error[:300]}")