"""Judge rubrics and ensemble scoring.

Three axes:
  - prompt: does the reviewer's guess match the original request?
  - diff:   does the reviewer's guess describe what actually changed?
  - quality: does the change fulfill the original request?

Each judge returns {"score": 0-3, "reasoning": str}.
Ensemble runs N models, returns {"median": int, "spread": int, "individual": {...}}.
"""
from __future__ import annotations

import json
import re
import statistics
from typing import Callable

from anthropic import Anthropic

PROMPT_AXIS_SYS = (
    "You are scoring how well an LLM reviewer guessed the intent of a code change, "
    "given only the diff. Use the full 0-10 range. Be precise — small differences "
    "in guess quality should yield small differences in score."
)
PROMPT_AXIS_RUBRIC = (
    "Rubric (0-10, use the full range):\n"
    "   0 — completely unrelated; wrong topic entirely\n"
    "   2 — same topic but wrong direction (e.g., loosening instead of tightening)\n"
    "   4 — right general area but the specifics are off or absent\n"
    "   6 — close match; captures the action but misses one meaningful element\n"
    "   8 — matches action, target, and scope with minor imprecision or over-specificity\n"
    "  10 — identical intent: guess precisely describes the request\n"
    "\n"
    "Use intermediate values (1, 3, 5, 7, 9) for in-between cases. Do NOT default to round numbers."
)

DIFF_AXIS_SYS = (
    "You are evaluating how faithfully a reviewer's guess describes the "
    "changes visible in a code diff. The reviewer saw only the diff and "
    "produced this one-sentence guess of what the author was trying to do. "
    "You do NOT have access to the author's original request. "
    "Score whether the guess captures what was ACTUALLY changed in the diff. "
    "Use the full 0-10 range; be precise."
)
DIFF_AXIS_RUBRIC = (
    "Rubric (fidelity to diff, 0-10, use the full range):\n"
    "   0 — guess contradicts or is inconsistent with the diff\n"
    "   2 — guess captures only a small portion; most significant changes unmentioned\n"
    "   4 — guess covers about half of the material elements; key parts missing\n"
    "   6 — covers most material elements but misses one or includes one wrong detail\n"
    "   8 — faithful description with minor omissions of non-critical details\n"
    "  10 — complete and precise description of every material element in the diff\n"
    "\n"
    "Use intermediate values for in-between cases. Do NOT default to round numbers."
)

QUALITY_AXIS_SYS = (
    "You evaluate whether a code change correctly fulfills a change request. "
    "You see the original request and the diff (before/after). "
    "Score whether the change ADDRESSES the request. "
    "Bonus additions beyond the request are fine as long as the core request is met. "
    "Use the full 0-10 range; be precise."
)
QUALITY_AXIS_RUBRIC = (
    "Rubric (change quality, 0-10, use the full range):\n"
    "   0 — diff goes in the opposite direction of the request\n"
    "   2 — addresses something tangential; core request not fulfilled\n"
    "   4 — partially fulfills the request; misses or misinterprets one important element\n"
    "   6 — mostly fulfills the request; minor gaps or deviations remain\n"
    "   8 — fulfills the request with minor imprecision or over-engineering\n"
    "  10 — precisely and completely fulfills the request\n"
    "\n"
    "Use intermediate values for in-between cases. Do NOT default to round numbers."
)

_JSON_TAIL = (
    "\n\nIMPORTANT: Respond with raw JSON only. No markdown fences, "
    "no preamble, no explanation outside the JSON. Format:\n"
    '{"score": 0, "reasoning": "one sentence"}'
)


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[^{}]*?\"score\"[^{}]*?\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    sm = re.search(r'"?score"?\s*[:=]\s*(\d)', text)
    rm = re.search(r'"?reasoning"?\s*[:=]\s*"?([^"\n]+)"?', text)
    if sm:
        return {
            "score": int(sm.group(1)),
            "reasoning": rm.group(1).strip().rstrip('"') if rm else "(unparsed)",
        }
    raise ValueError(f"could not extract JSON from: {text[:200]!r}")


def _call(client: Anthropic, model: str, system: str, user: str) -> dict:
    r = client.messages.create(
        model=model,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user + _JSON_TAIL}],
    )
    return _extract_json(r.content[0].text)


def judge_prompt_axis(client: Anthropic, model: str, original_prompt: str, guess: str) -> dict:
    user = (
        f"Original change request:\n{original_prompt}\n\n"
        f"Reviewer's guess:\n{guess}\n\n"
        f"{PROMPT_AXIS_RUBRIC}"
    )
    return _call(client, model, PROMPT_AXIS_SYS, user)


def judge_diff_axis(client: Anthropic, model: str, diff: str, guess: str) -> dict:
    user = (
        f"Diff:\n{diff}\n\n"
        f"Reviewer's guess:\n{guess}\n\n"
        f"{DIFF_AXIS_RUBRIC}"
    )
    return _call(client, model, DIFF_AXIS_SYS, user)


def judge_quality_axis(client: Anthropic, model: str, original_prompt: str, diff: str) -> dict:
    user = (
        f"Original change request:\n{original_prompt}\n\n"
        f"Actual diff:\n{diff}\n\n"
        f"{QUALITY_AXIS_RUBRIC}"
    )
    return _call(client, model, QUALITY_AXIS_SYS, user)


def ensemble(judge_fn: Callable, client: Anthropic, models: dict[str, str], *args) -> dict:
    individual: dict[str, dict] = {}
    for name, model in models.items():
        try:
            individual[name] = judge_fn(client, model, *args)
        except Exception as e:
            individual[name] = {"score": -1, "reasoning": f"ERROR: {type(e).__name__}: {e}"}
    scores = [s["score"] for s in individual.values() if s["score"] >= 0]
    if not scores:
        return {"median": -1, "mean": -1.0, "spread": 0, "individual": individual}
    # Median preserves fractional values in 0-10 range.
    return {
        "median": round(statistics.median(scores), 1),
        "mean": round(statistics.mean(scores), 2),
        "spread": max(scores) - min(scores),
        "individual": individual,
    }
