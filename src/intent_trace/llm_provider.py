"""Unified LLM provider interface: Anthropic + OpenAI.

Model ID dispatch:
  - claude-*         → Anthropic
  - gpt-*, o1-*, o3-*, o4-*, chatgpt-* → OpenAI
"""
from __future__ import annotations

from functools import lru_cache


def is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o2", "o3", "o4", "chatgpt-"))


@lru_cache(maxsize=1)
def _anthropic_client():
    from anthropic import Anthropic
    return Anthropic()


@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI
    return OpenAI()


def call_llm(model: str, system: str, user: str, max_tokens: int = 512) -> str:
    """Send a single-turn system+user prompt, return assistant text."""
    if is_openai_model(model):
        r = _openai_client().chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return r.choices[0].message.content or ""
    # Anthropic default
    r = _anthropic_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text
