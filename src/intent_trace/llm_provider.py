"""Unified LLM provider interface: Anthropic + OpenAI + Google + Moonshot + local Ollama.

Model ID dispatch:
  - claude-*                           → Anthropic
  - gpt-*, chatgpt-*, o1-*, o3-*, o4-* → OpenAI
  - gemini-*, text-bison, chat-bison   → Google
  - kimi-*, moonshot-*                 → Moonshot (OpenAI-compatible API)
  - anything with ":" (e.g. gemma4:e4b) → local Ollama (HTTP to localhost:11434)
"""
from __future__ import annotations

from functools import lru_cache


def is_openai_model(model: str) -> bool:
    return model.startswith(("gpt-", "chatgpt-", "o1-", "o3-", "o4-"))


def is_google_model(model: str) -> bool:
    return model.startswith(("gemini-", "text-bison", "chat-bison"))


def is_moonshot_model(model: str) -> bool:
    return model.startswith(("kimi-", "moonshot-"))


def is_ollama_model(model: str) -> bool:
    # Ollama model IDs use "name:tag" format (gemma4:e4b, llama3:70b, etc.).
    # Cloud model IDs never contain ":".
    return ":" in model


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


@lru_cache(maxsize=1)
def _moonshot_client():
    import os
    from openai import OpenAI
    return OpenAI(api_key=os.environ["MOONSHOT_API_KEY"],
                  base_url="https://api.moonshot.ai/v1")


def call_llm(model: str, system: str, user: str, max_tokens: int = 512) -> str:
    """Send a single-turn system+user prompt, return assistant text."""
    if is_ollama_model(model):
        # Ollama local inference. Gemma (at least gemma4:e4b) silently ignores
        # the system role — merge system into user so instructions are honored.
        import requests
        combined = f"{system}\n\n{user}" if system else user
        r = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": combined}],
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=180,
        )
        r.raise_for_status()
        return r.json()["message"]["content"] or ""
    if is_google_model(model):
        from google.genai import types
        # Gemini 2.5 Pro is always-thinking — setting thinking_budget=0 raises
        # INVALID_ARGUMENT. For Flash and others, explicitly disable thinking so
        # output tokens aren't silently consumed by the reasoning chain.
        is_pro = model.startswith("gemini-2.5-pro")
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            **({} if is_pro else {"thinking_config": types.ThinkingConfig(thinking_budget=0)}),
        )
        r = _google_client().models.generate_content(
            model=model, contents=user, config=config,
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
    if is_moonshot_model(model):
        r = _moonshot_client().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
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
