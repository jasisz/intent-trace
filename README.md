# intent-trace

A benchmark for measuring how much author intent survives a code change diff.

## Method

1. **LLM-A** receives a program and a change request. Produces a modified program.
2. **LLM-B** sees only the **diff** (before/after). Guesses in one sentence what the author was trying to achieve. Never sees the original request.
3. **Judge** LLM scores guess vs original request on a 0–3 rubric (unrelated / wrong specifics / close / matches).

Comparison axes:

- **Language** — first pair: Aver, Python. Same change request, equivalent program in each.
- **Ablation** — full diff vs impl-only (prose/narrative layer stripped). Delta isolates the signal carried by language-specific metadata (Aver: `intent`, `decision`, `?` descriptions, `verify`; Python: docstrings, comments).

The benchmark tests whether structured intent layers in Aver transmit author intent more reliably than free-form Python prose, after controlling for how much extra text is present.

## Status

Early WIP. First end-to-end slice on `order_total`.

## Running

```bash
uv sync
uv run --env-file .env scripts/run_one.py programs/order_total prompts/order_total/reject_negative_prices.md
```

## Bias declaration

Built to test claims about [Aver](https://github.com/jasisz/aver). Trying to falsify them, not confirm. PRs adding more languages welcome.
