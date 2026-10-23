"""Thin Ollama client with graceful degradation.

Every call returns ``None`` on failure so the API layer can fall back to a
templated answer instead of surfacing an error to the booth visitor.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are {assistant}, the virtual banking assistant for {bank}.
You are speaking with {customer}. Be concise, courteous and professional, like a bank employee.
Answer ONLY from the CONTEXT below. If the answer is not in the context, say you do not have that
information and suggest contacting a Northwind banker at 1-800-555-0100. Never invent fees, rates
or policies. Do not mention that you were given context. Use plain sentences, no markdown headers.

CONTEXT:
{context}
"""


def is_available(timeout: float = 3.0) -> dict:
    """Return {'available': bool, 'models': [...], 'error': str|None}."""
    if not settings.llm_enabled:
        return {"available": False, "models": [], "error": "LLM disabled (LLM_ENABLED=false)"}
    try:
        resp = httpx.get(f"{settings.ollama_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return {"available": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        return {"available": False, "models": [], "error": str(exc)}


def generate(question: str, context: str, customer_name: str) -> str | None:
    if not settings.llm_enabled:
        return None
    system = SYSTEM_PROMPT.format(
        assistant=settings.assistant_name,
        bank=settings.bank_name,
        customer=customer_name,
        context=context or "(no relevant documents found)",
    )
    payload = {
        "model": settings.ollama_chat_model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        "options": {"temperature": 0.2, "num_predict": settings.llm_max_tokens},
        "keep_alive": "30m",
    }
    try:
        resp = httpx.post(f"{settings.ollama_url}/api/chat", json=payload, timeout=settings.llm_timeout_s)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "").strip()
        return content or None
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM generation failed, falling back to template: %s", exc)
        return None
