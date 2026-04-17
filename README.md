# intent-trace

**An empirical benchmark for how much change-level author intent survives a code diff — measured with an LLM reviewer.**

This repo tests a claim from the [Aver language](https://averlang.dev) project: that **structurally declared intent** (signatures, description markers, decision blocks, verify blocks) makes code legible for AI review without special training on the language.

**What we measure is *intent of the diff*, not *intent of the program*.** The reviewer sees a unified diff between a baseline and a refactored snapshot and has to reconstruct what the author was trying to *change*. This is a different question from "given the full codebase, what does this program do" — Aver may have stronger or weaker properties for whole-program comprehension than this benchmark can show. We deliberately stay in the PR-review frame: reviewer sees the diff, nothing more.

Two headline findings:

1. **On small-to-medium programs (≤400 lines), Aver reaches parity or edges ahead of heavily-documented Python transliteration on strong readers (Claude Sonnet and gpt-4.1), despite Claude/OpenAI having essentially zero training on Aver.** An earlier 3-judge (Claude-only) rerun suggested a symmetric home-field advantage; the 5-judge cross-vendor rerun collapsed most of that signal, pointing to **judge-family bias more than reader-family bias** as the dominant confound.

2. **On the largest program (`payment_ops`, ~1300 lines, real multi-module domain), heavily-documented Python pulls clearly ahead — by 0.60 on average.** `python_oop` (sparse docstrings) stays near Aver, so it is **docstring volume, not Python as a language**, that wins at scale. Aver's structured-prose format saturates at small sizes; paragraph-scale docstrings keep adding review-relevant context in big diffs.

Across all readers and program sizes, Aver depends heavily on its prose layer (`intent`, `decision`, `?`, `verify`) — masking it produces the largest ablation drop of any variant, and `aver/masked` is the weakest cell of the six on every reader.

## Method in one paragraph

For each `(program, prompt, language, view)` cell, an agent applies the change request to a baseline program, we compute the unified diff, hand it to a **reviewer LLM** (Claude Sonnet 4.6) asking it to guess the author's intent, and score the guess along two axes with a **5-model cross-vendor ensemble** (Claude Opus 4.7 + Sonnet 4.6 + Haiku 4.5 + OpenAI gpt-4o + gpt-4.1, median). Every rubric uses a **0–10 scale with descriptor anchors**. Append-only JSONL output, crash-safe, resumable.

## What we measured

### Two scoring axes per slice

- **Prompt-axis (P)** — how well the reviewer's guess matches the original change request.
- **Diff-axis (D)** — how faithfully the reviewer's guess describes what actually changed in the code.

(A third axis, *change quality*, is also computed per `(prompt, lang)` but is not the primary signal; see `src/intent_trace/judges.py`.)

### Two views per slice

- **full** — the unified diff as a PR reviewer sees it (before/after, 3 lines of context around changes).
- **masked** — same diff, but with the **prose layer stripped**: in Aver that's `intent = …`, `decision …`, `? "…"` descriptions, and `verify …` blocks; in Python that's docstrings and `#` comments. Ablation tells us what each language's prose layer actually transmits.

### Three language variants

- **aver** — the original.
- **python_from_aver** — **an intentionally non-idiomatic transliteration baseline.** It is Python syntax carrying Aver-style affordances: frozen dataclasses, pure functions, `replace()`-based updates, camelCase names, small composable helpers, and heavy docstrings lifted from Aver's `?` descriptions. Its purpose is *not* to model typical Python — it is to isolate how much review legibility comes from the carrier language versus the preserved intent structure. Treating it as "what a Python dev would write" is a misreading.
- **python_oop** — an independent OOP Python design of the same programs (classes with methods, mutation where natural, snake_case, typed exception hierarchy, **sparse** ~30–40% docstring coverage — classical idiomatic Python, the "Python a Python dev would actually write" target).

Both Python variants cover the same domains as the Aver version and their smoke tests pass independently. The three variants give a triangulation: language idiom × prose density × structural decomposition.

## Programs

| program | description | size |
|---|---|---|
| `inventory` | warehouse + SKU + reservations + reorder | ~250 lines Aver, 5 prompts |
| `workflow` | expense-report approval state machine | ~200 lines Aver, 5 prompts |
| `taskmanager` | multi-module (models / validation / projects / tasks / main) | ~400 lines Aver, 4 prompts |
| `payment_ops` | real multi-module domain: webhook normalize, cases, ledger, reconcile, views | ~1300 lines Aver, 4 prompts |

Prompts range from specific refactors ("reject negative prices") to vague directives ("make it harder to lose stock"), including one large architectural change per program.

## Results

**All numbers below use the 5-judge cross-vendor ensemble (Claude Opus 4.7 + Sonnet 4.6 + Haiku 4.5 + OpenAI gpt-4o + gpt-4.1, median), applied to every slice of every reader. 324 measured slices total (3 readers × 18 prompts × 3 languages × 2 views, N=18 per (lang, view) cell).**

### Per-reader ranking (full view, avg = (P+D)/2)

```
Claude Sonnet 4.6 (reader):
  1. aver              full    8.61
  2. python_from_aver  full    8.58
  3. python_oop        full    8.26

OpenAI gpt-4.1 (reader):
  1. aver              full    8.54
  2. python_from_aver  full    8.44
  3. python_oop        full    8.40

Google Gemini 2.5 Flash (reader):
  1. python_from_aver  full    8.42
  2. python_oop        full    8.14
  3. aver              full    7.92
```

**Aver wins on the two strongest readers; loses to heavy-doc Python only under the weakest (Gemini Flash).** The top-2 gap under Sonnet is 0.03 points (within rubric noise). Under gpt-4.1 the top-3 span is 0.14 points — essentially a three-way tie. Gemini is the one reader where Python-from-Aver has a clear lead (+0.50 over Aver).

### Full cross-reader comparison

![three-way readers](results/plots/three_way_readers.png)

```
lang/view                Sonnet      gpt-4.1     Gemini
aver/full                8.61 (18)   8.54 (18)   7.92 (18)
aver/masked              8.07 (18)   7.79 (18)   7.43 (18)   ← always lowest
python_from_aver/full    8.58 (18)   8.44 (18)   8.42 (18)   ← most stable
python_from_aver/masked  8.32 (18)   8.17 (18)   7.92 (18)
python_oop/full          8.26 (18)   8.40 (18)   8.14 (18)
python_oop/masked        8.22 (18)   8.38 (18)   7.89 (18)   ← barely moves
```

### 3-judge vs 5-judge — the rerun that changed the ranking

![3j vs 5j](results/plots/judges_3v5.png)

### Judge × reader grid (who is kind to whom)

![judge reader heatmap](results/plots/judge_reader_heatmap.png)

### Judge family × language (GPT is kinder to Aver)

![judge family by language](results/plots/judge_family_x_lang.png)

### Ablation across all readers

![ablation all readers](results/plots/ablation_all_readers.png)

### Judge-family bias vs reader-family bias

The earlier 3-judge (Claude-only) run showed a clean symmetric home-field pattern (Claude → Aver wins, gpt-4.1 → python_oop wins). Adding gpt-4o and gpt-4.1 as judges made that symmetry mostly disappear. The gpt-4.1 reader now ranks **Aver first (8.54)** under cross-vendor judging — reversing the earlier 3-judge Claude-only result (which had python_oop first at 8.50). This points to the effect being **a judge-panel bias**, not an intrinsic reader-family preference.

Measured directly:

```
Judge family   Sonnet reader   gpt-4.1 reader   Gemini reader   Sonnet−gpt-4.1
Claude-3         8.46            8.41             8.09           +0.06   ← leans Sonnet
OpenAI-2         8.55            8.57             8.20           -0.02   ← leans gpt-4.1
```

Each judge family does score its own-vendor reader slightly higher, but the margin is ~0.05 — much smaller than the rubric noise band (≥0.3 per-cell spread). GPT judges are also uniformly ~0.1 more lenient than Claude judges on every reader.

Where GPT and Claude judges differ most is by **language style**, not reader:

```
lang                Claude-3 avg   GPT-2 avg   Δ (GPT − Claude)
aver                  8.26           8.48        +0.22   ← GPT judges notably kinder to Aver guesses
python_from_aver      8.47           8.54        +0.07
python_oop            8.23           8.30        +0.07
```

Claude is the stricter grader on Aver guess-quality. Once GPT judges are added back, Aver's relative position improves the most — which is why the 3-judge → 5-judge transition reshuffled the top of the table.

### Per-program — where Aver wins, where Python wins

![per-program ranking](results/plots/program_lang_ranking.png)

```
program        lines   aver   py_from   py_oop   Δ aver−oop   Δ aver−pfa
inventory      ~250   8.53     8.57     8.43      +0.10       -0.03     ← tie
workflow       ~200   8.50     8.37     8.28      +0.22       +0.13     ← Aver wins
taskmanager    ~400   8.12     8.21     8.23      -0.10       -0.08     ← Python wins
payment_ops   ~1300   8.19     8.79     8.08      +0.10       -0.60     ← heavy-doc Python dominates
```

**Aver's lead over heavy-doc Python (`python_from_aver`) inverts with program size.** On the small/medium programs (inventory, workflow) Aver edges ahead by 0.13–0.22 or ties. On the largest, multi-module `payment_ops` (~1300 lines, real-world domain shape) `python_from_aver` wins by a clear 0.60 across all three readers. `taskmanager` (~400 lines, multi-module) already shows the flip.

The most likely reason: Aver's prose layer is dense per construct, but Python's docstrings in the `python_from_aver` baseline can be paragraph-length — and on a 1300-line diff touching many modules, a reviewer benefits more from expansive natural-language context than from terse structured markers. Idiomatic OOP Python (`python_oop`) stays near Aver but below `python_from_aver` on `payment_ops`, suggesting **it is docstring density, not language choice, driving that gap.**

### Program complexity × language advantage

![complexity advantage](results/plots/program_complexity_advantage.png)

### Program × language × reader (full view heatmap)

![program reader heatmap](results/plots/program_reader_heatmap.png)

### Cross-vendor judge alignment (Sonnet reader, per-judge mean)

```
judge      P-axis   D-axis
opus        8.33     8.61
sonnet      7.76     8.69
haiku       7.86     8.81
gpt-4o      7.53     9.36
gpt-4.1     7.66     9.02
```

OpenAI judges are systematically *stricter* on prompt-match (−0.3 to −0.4) and *more lenient* on diff-fidelity (+0.3 to +0.6). The biases partly cancel in the averaged (P+D)/2 headline.

### Per-axis breakdown

![axes](results/plots/axes.png)

### Ablation — what strip of prose reveals (Sonnet reader, 5-judge)

![ablation](results/plots/ablation.png)

```
Aver              full → masked   ΔP = -0.94   ΔD = -0.14
Python (from Aver) full → masked   ΔP = -0.50   ΔD = -0.03
Python (OOP)      full → masked   ΔP = -0.06   ΔD = -0.03
```

The pattern replicates across every reader: `aver/masked` is the lowest-scoring cell of all six under Sonnet (8.07), gpt-4.1 (7.79), and Gemini (7.43). Aver concentrates intent in prose that a reviewer cannot reconstruct from the code alone. Idiomatic OOP Python has very little to lose.

## Findings

Read these as statements about the specific setup described above — 3 reader families, 5 judge models spanning 3 vendors, 3 agent-generated baselines, 18 prompts across 4 programs — not as universal claims.

1. **Parity between Aver and heavy-doc Python is real for strong readers.** Under Claude Sonnet reader and gpt-4.1 reader, `aver/full` and `python_from_aver/full` are within 0.03 and 0.10 points respectively — both below rubric noise. Aver also beats idiomatic OOP Python under both (by 0.35 and 0.14). Under Gemini Flash, python_from_aver wins by a clear 0.50 — but all three language variants score lower there, suggesting reader capability is the bottleneck, not a language bias.

2. **"Home-field advantage" was mostly a judge-panel artifact.** The earlier 3-judge (Claude-only) panel made gpt-4.1 rank python_oop first (8.50) and Aver third (8.36). Swapping in a 5-judge cross-vendor panel put Aver first (8.54) on the same gpt-4.1 reader. Judge-family bias (Claude vs GPT means per reader) is only +0.06, within noise. This is the single biggest methodological lesson from the rerun.

3. **GPT judges are notably kinder to Aver than Claude judges are.** On `(P+D)/2` averaged over all readers: Aver language +0.22 in GPT judges vs Claude; `python_from_aver` and `python_oop` only +0.07. Claude is the stricter grader on Aver guess-quality. This is why the judge-panel swap had the biggest effect on the Aver ranking.

4. **Aver concentrates intent in the prose layer.** Strip `intent` / `decision` / `?` descriptions / `verify` blocks and Aver drops the most of any variant (−0.94 P-axis). `aver/masked` is the lowest-scoring cell of all six on every reader (8.07 / 7.79 / 7.43). This is the expected shape of the language's legibility claim — the prose is load-bearing, and the ablation proves *where* Aver's signal lives.

5. **Idiomatic OOP Python (sparse docstrings) is the "typical codebase" baseline.** Full and masked both ~8.2–8.4 on Claude/gpt-4.1 — stable, slightly below the prose-heavy variants on strong readers. No prose to lose; what you see in the code is what you get. This is what most real Python codebases look like.

6. **Ablation asymmetry reveals what each prose style actually transmits** (Sonnet reader):
   - **Aver prose** → *prompt-match precision* (P drops 0.94, D barely moves)
   - **Python-from-Aver docstrings** → *prompt-match precision too* (P drops 0.50, D stable)
   - **Python-OOP sparse docs** → *redundant ornament on code-level signal* (no measurable drop)

7. **Aver's legibility edge is program-size dependent.** On `inventory` and `workflow` (~200–250 lines, single-module) Aver wins or ties every Python variant on every reader. On `taskmanager` (~400 lines, multi-module) Aver already falls behind. On `payment_ops` (~1300 lines, real multi-module domain) **`python_from_aver` beats Aver by 0.60** on average — the gap is consistent across all three readers, and `python_oop` stays near Aver, so it is **docstring volume, not Python itself, that pulls ahead at scale**. The structured-prose format Aver uses saturates at small sizes; paragraph-scale Python docstrings keep adding information in big diffs. Whether Aver can close this gap with richer module-level `intent` blocks is an open question this benchmark doesn't answer.

## Why this experiment even exists

[Aver's thesis](https://averlang.dev) is that code must be **legible to an AI reviewer** — that the artifact carries intent so a reviewer (human or AI) can reconstruct it without prior familiarity. This benchmark operationalizes that claim: we treat an LLM as the reviewer, measure how much intent it can reconstruct from each artifact style, and compare across training-exposure asymmetries.

The headline: **Aver's structural intent declarations (`intent`, `decision`, `?`, `verify`) are a functional equivalent for Python's heavy docstring tradition, from the perspective of an AI review — at the size range of a typical single module (≤400 lines). On small/medium programs Aver ties or edges ahead of idiomatic OOP Python on strong readers, despite zero training presence. On a 1300-line multi-module domain (`payment_ops`), paragraph-scale Python docstrings pull ahead by 0.60 — a clean demonstration that prose volume matters more than prose format at scale.** The prose layer is load-bearing across the board (masked Aver is the weakest cell everywhere); the remaining question is whether richer module-level `intent` declarations can close the large-program gap.

## Threats to validity

These are the reasons to treat the numbers as *indicative* rather than *conclusive*, in rough order of impact.

1. **The benchmark is diff-review, not program comprehension.** We measure how well a reviewer reconstructs the *intent of a change* from a unified diff. We do not measure how well a reviewer understands the *intent of a whole program* given its full source. Aver has affordances for whole-program understanding (module `intent`, `decision` blocks, `aver context` tool) that this benchmark simply does not exercise. A Python-OOP program with a terse diff may still be harder to understand at the program level than an Aver program — that's a different, un-tested question.

2. **Small domain coverage.** Four programs, 18 prompts total. Three are small/medium invented domains (inventory, workflow, taskmanager); one is a real-world multi-module domain (payment_ops, ~1300 lines). Typical enterprise codebases are 10k+ lines across dozens of modules; behavior at that scale is not tested.

3. **Agent-produced refactors, not human commits.** Both the baselines and the refactors were generated by sub-agents (Claude Code) applying the change requests. Real open-source commits from human developers would reduce construct bias, but require domain-matched Aver corpora that don't yet exist (Aver is a new language).

4. **`python_from_aver` is a transliteration baseline, not typical Python** (see above). Any reader who treats it as "what a Python dev would write" will over-read the Aver-vs-Python framing. The intended comparison is three-way (`aver` / `python_from_aver` / `python_oop`), not binary.

5. **Rubric is an LLM judging another LLM.** The 0–10 score is itself a model output, not ground truth. Two shapes of judge bias were measured directly: (a) judge-family preference for own-vendor reader is only +0.06 (within rubric noise); (b) GPT judges score Aver guesses +0.22 higher than Claude judges do (this is why the 3-judge → 5-judge transition reshuffled the top of the table). OpenAI judges are systematically stricter on prompt-axis and more lenient on diff-axis; the two partly cancel in the averaged headline but the composition of the judge panel is material. No human raters.

6. **Docstring volume is a live confound.** `python_from_aver` ships with extensive paragraph-length docstrings inherited from Aver's `?` descriptions — often richer per function than the Aver prose alone. On `payment_ops` the `python_from_aver` docstring payload is substantially larger (in characters) than the Aver prose payload for the same program. The benchmark does not normalize for prose length, so the large-program gap we report is a combined effect of *language format* and *prose volume*. A shorter-doc control (or a richer Aver-prose variant with module-level decision blocks) would disentangle these.

7. **Gemini 2.5 Flash is the weakest of the three readers.** It scores every language variant ~0.3–0.7 below Sonnet and gpt-4.1 readers. This looks more like limited reader capability than a language preference — but it means the one reader where Python-from-Aver clearly beats Aver is also the reader with the weakest overall reconstruction. A Gemini-Pro rerun would clarify.

8. **`aver context` tool was tested and removed.** We initially ran an `aver_context` view (diffing compressed intent summaries) but concluded the setup was synthetic — `aver context` is a state-projection tool, not a change-representation tool, and diffing two projections doesn't match how reviewers actually use the tool. Scores collapsed in complex multi-module code for structural (not legibility) reasons. Code path dropped; historical JSONL entries from that view are not included in the headline numbers.

## Reproduce

```bash
# Install + API keys
uv sync
# .env needs: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY

# Full pipeline for one reader (LLM-B = INTENT_TRACE_MODEL_B env var)
INTENT_TRACE_MODEL_B=claude-sonnet-4-6 \
  uv run --env-file .env scripts/run_all.py
# (swap for gpt-4.1 or gemini-2.5-flash to produce the other reader datasets)

# Cross-vendor 4th/5th judges on an existing run (reuse diffs+guesses)
INTENT_TRACE_JUDGE_GPT=gpt-4o uv run --env-file .env \
  scripts/add_gpt_judge.py results/ensemble_YYYYMMDD_HHMMSS/slices.jsonl
INTENT_TRACE_JUDGE_GPT=gpt-4.1 uv run --env-file .env \
  scripts/add_gpt_judge.py <above output>/slices.jsonl

# Merge resume runs into canonical per-reader JSONL
uv run python scripts/merge_judge_runs.py            # → results/merged/{sonnet,gpt4.1,gemini}.jsonl
# Fill judges that a prior run missed (e.g. 503s)
uv run --env-file .env python scripts/fill_missing_judges.py all
# Re-run LLM-B + 5 judges on slices a reader skipped
uv run --env-file .env python scripts/fill_missing_slices.py gemini

# Plots
uv run python scripts/plot_results.py     # ranking, axes, ablation, 3-way reader
uv run python scripts/plot_heatmaps.py    # reader × program × lang × view heatmaps
```

## Layout

```
programs/<program>/
  aver/before/*.av                           # original Aver baseline
  aver/after/<prompt>/*.av                   # refactor applied by agent
  python_from_aver/before/*.py               # Aver-translated-style Python baseline
  python_from_aver/after/<prompt>/*.py
  python_oop/before/*.py                     # independent OOP Python baseline
  python_oop/after/<prompt>/*.py
prompts/<program>/*.md                       # change request per prompt

src/intent_trace/
  judges.py                                  # 3 rubrics + ensemble median
  mask.py                                    # prose-stripping for ablation

scripts/
  run_all.py                                 # pipeline entrypoint (LLM-B + judges)
  add_gpt_judge.py                           # append a cross-vendor judge to a finished run
  fill_missing_judges.py                     # patch missing GPT judges on merged datasets
  fill_missing_slices.py                     # re-run LLM-B + 5 judges for slices a reader dropped
  merge_judge_runs.py                        # collapse resume runs into one canonical JSONL per reader
  plot_results.py                            # ranking / axes / ablation / 3-way reader charts
  plot_heatmaps.py                           # reader × program × lang × view heatmaps

results/ensemble_<timestamp>/                # raw pipeline outputs (one per reader × model-B run)
  meta.json                                  # plan + model IDs
  slices.jsonl                               # one slice per line (append-only, crash-safe)
  SUCCESS                                    # marker after clean finish
results/gpt_added_<timestamp>/               # add_gpt_judge.py outputs
results/merged/<reader>.jsonl                # canonical per-reader dataset (deduped, 5-judge)
```

## Status

Dataset is complete: 3 readers × 108 prompt/diff slices × 5 judges × 2 axes = 3240 judgments, plus 3 readers × 54 quality slices × 3 Claude judges = 486 more, for 3726 scored data points. N=18 per `(lang, view)` cell on every reader. Findings above are stable under ensemble noise (~1 point spread on prompt-axis, 0–1 on diff-axis). PRs welcome to add languages, models, or programs.

## License

MIT.
