from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .config import settings


logger = logging.getLogger(__name__)


class TransientHTTPException(Exception):
    pass


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, TransientHTTPException):
        return True
    return False


@retry(
    stop=stop_after_attempt(settings.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable_exception),
    reraise=True,
)
async def generate_reply(
    text: str,
    system_prompt: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Call Gemini to generate a reply for given user text.

    Uses Google Generative Language API generateContent endpoint for the configured model.
    """
    base_url = "https://generativelanguage.googleapis.com"
    url = f"{base_url}/v1beta/models/{settings.GEMINI_MODEL}:generateContent"

    contents: List[Dict[str, Any]] = []
    if history:
        for msg in history:
            role = msg.get("role", "user")
            msg_text = msg.get("text", "")
            if not msg_text:
                continue
            contents.append({"role": role, "parts": [{"text": msg_text}]})

    contents.append({"role": "user", "parts": [{"text": text}]})

    payload: Dict[str, Any] = {
        # Using snake_case per requested spec
        "system_instruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": contents,
        "generation_config": {
            "temperature": 0.7,
            "max_output_tokens": 300
        }
    }

    headers = {"Content-Type": "application/json"}
    params = {"key": settings.GEMINI_API_KEY}

    async with httpx.AsyncClient(timeout=settings.TIMEOUT_S) as client:
        try:
            resp = await client.post(url, headers=headers, params=params, json=payload)
            if resp.status_code == 429:
                # Surface as retryable
                raise httpx.HTTPStatusError("Rate limited", request=resp.request, response=resp)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            logger.warning("Gemini request failed (will retry if retryable): %s", e)
            raise
        except httpx.HTTPError as e:
            # Network level errors
            logger.warning("HTTP error to Gemini: %s", e)
            raise TransientHTTPException(str(e)) from e

    try:
        # Typical shape: candidates[0].content.parts[0].text
        candidates = data.get("candidates")
        if not candidates:
            raise ValueError("No candidates in response")
        content = candidates[0].get("content")
        parts = content.get("parts") if content else None
        if not parts or not parts[0].get("text"):
            raise ValueError("No text parts in response")
        return parts[0]["text"]
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to parse Gemini response: %s | payload=%s", e, data)
        raise


