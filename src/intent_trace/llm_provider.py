"""Unified LLM provider interface: Anthropic + OpenAI.

Model ID dispatch:
  - claude-*         → Anthropic
  - gpt-*, o1-*, o3-*, o4-*, chatgpt-* → OpenAI
"""
from __future__ import annotations

from functools import lru_cache


def is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "chatgpt-", "o1-", "o3-", "o4-"))


def is_google_model(model: str) -> bool:
    return model.startswith(("gemini-", "text-bison", "chat-bison"))


@lru_cache(maxsize=1)
def _anthropic_client():
    from anthropic import Anthropic
    return Anthropic()


@lru_cache(maxsize=1)
def _openai_client():
    from openai import OpenAI
    return OpenAI()


@lru_cache(maxsize=1)
def _google_client():
    import os
    from google import genai
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


def call_llm(model: str, system: str, user: str, max_tokens: int = 512) -> str:
    """Send a single-turn system+user prompt, return assistant text."""
    if is_google_model(model):
        from google.genai import types
        # Disable thinking so output tokens aren't silently consumed by
        # the reasoning chain (Gemini 2.5 enables thinking by default).
        r = _google_client().models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        return r.text or ""
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
