"""Aggressive ablation: strip prose/narrative layers from source files.

Aver (default): strip `intent = ...`, `? "..."` descriptions, `decision ...`
      blocks, `verify ...` and `verify ... law ...` blocks.
Python: strip module and function docstrings, comments (via ast.unparse),
        `_smoke*` functions, and `if __name__ == "__main__":` blocks.

Note on `verify` blocks: these are executable specifications in Aver, closer
to Python `assert` than to a docstring. Stripping them for the "masked" view
is therefore an aggressive choice — it treats spec-as-prose. Set
`keep_verify=True` (or pass lang="aver_keep_verify") to preserve them while
still removing the narrative (`intent` / `?` / `decision`). The current
dataset was scored with keep_verify=False; a follow-up run with
keep_verify=True would disentangle prose ablation from spec ablation.

Goal: remove human-targeted narrative while preserving executable/typed
structure, so diffs show only code-level change.
"""
from __future__ import annotations

import ast


def mask_aver(source: str, keep_verify: bool = False) -> str:
    lines = source.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    def indent_of(s: str) -> int:
        return len(s) - len(s.lstrip())

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            out.append(line)
            i += 1
            continue

        ind = indent_of(line)

        # Top-level anchor?
        if ind == 0:
            first = stripped.split()[0]

            # Full-block strip: decision always; verify only when not kept
            if first == "decision" or (first == "verify" and not keep_verify):
                i += 1
                while i < n:
                    nxt = lines[i]
                    if nxt.strip() == "" or indent_of(nxt) > 0:
                        i += 1
                    else:
                        break
                continue

            # Keep line; process body for module / fn
            out.append(line)
            i += 1

            if first in ("module", "fn"):
                while i < n:
                    inner = lines[i]
                    inner_stripped = inner.strip()

                    # Break on next top-level anchor
                    if inner_stripped and indent_of(inner) == 0:
                        break

                    # module: strip `intent = ...` and its continuation
                    if first == "module" and inner_stripped.startswith("intent"):
                        intent_ind = indent_of(inner)
                        i += 1
                        while i < n:
                            cont = lines[i]
                            cs = cont.strip()
                            # continuation: a quoted line at deeper indent
                            if cs.startswith('"') and indent_of(cont) > intent_ind:
                                i += 1
                                continue
                            break
                        continue

                    # fn: strip `? "..."` (description) and its continuation quoted lines
                    if first == "fn" and inner_stripped.startswith("? "):
                        desc_ind = indent_of(inner)
                        i += 1
                        while i < n:
                            cont = lines[i]
                            cs = cont.strip()
                            if cs.startswith('"') and indent_of(cont) >= desc_ind:
                                i += 1
                                continue
                            break
                        continue

                    out.append(inner)
                    i += 1
            continue

        # Stray line (shouldn't happen at indent 0 after the anchor logic); pass through
        out.append(line)
        i += 1

    # Collapse 3+ consecutive blank lines to 2
    cleaned: list[str] = []
    blanks = 0
    for l in out:
        if l.strip() == "":
            blanks += 1
            if blanks <= 2:
                cleaned.append(l)
        else:
            blanks = 0
            cleaned.append(l)
    return "\n".join(cleaned)


def mask_python(source: str) -> str:
    tree = ast.parse(source)

    new_body: list[ast.stmt] = []
    for node in tree.body:
        # Drop module docstring
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue

        # Drop _smoke* test functions
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_smoke"):
            continue

        # Drop `if __name__ == "__main__":` block
        if isinstance(node, ast.If):
            t = node.test
            if (
                isinstance(t, ast.Compare)
                and isinstance(t.left, ast.Name)
                and t.left.id == "__name__"
            ):
                continue

        # Strip docstring from function bodies
        if isinstance(node, ast.FunctionDef):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] if len(node.body) > 1 else [ast.Pass()]

        new_body.append(node)

    tree.body = new_body
    ast.fix_missing_locations(tree)
    # ast.unparse drops comments automatically.
    return ast.unparse(tree)


def mask(lang: str, source: str) -> str:
    if lang == "aver":
        return mask_aver(source)
    if lang == "aver_keep_verify":
        return mask_aver(source, keep_verify=True)
    if lang.startswith("python"):  # python, python_oop, python_fp, ...
        return mask_python(source)
    raise ValueError(f"unknown lang: {lang}")
