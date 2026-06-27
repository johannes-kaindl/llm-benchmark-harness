# `ko_rule.red_flag_scope` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Checkbox steps.

**Goal:** A per-pack K.-o. switch `ko_rule.red_flag_scope: all|curated` so a quality pack (buero) only disqualifies on hallucination (curated baits + Q1 floor), not on any quality red flag — while ndassist's conservative "any red flag knocks out" stays the default.

**Architecture:** One optional `Literal` field on `KoRule` + a scope-aware branch in the central `scorecard.passes_ko`. All call sites already pass `pack`. Default `all` = byte-identical legacy behavior.

**Tech Stack:** Python 3.12, pydantic, pytest, ruff, mypy strict.

## Global Constraints

- Field: `KoRule.red_flag_scope: Literal["all", "curated"] = "all"`. Default preserves legacy behavior.
- `curated`: red-flag K.-o. fires only on `red_flagged ∩ ko.red_flag_prompts`; dimension floor unchanged in both scopes.
- ndassist (no field) → `all` → unchanged; `test_any_red_flag_knocks_out_even_uncurated` must stay green.

---

### Task 1: `KoRule.red_flag_scope` field (schema, TDD)

**Files:** Modify `touchstone/pack.py`; Test `tests/test_pack.py`.

- [ ] **Step 1: failing test** — append to `tests/test_pack.py`:

```python
def test_ko_red_flag_scope_defaults_to_all_and_validates():
    import pytest
    from pydantic import ValidationError
    from touchstone.pack import KoRule

    assert KoRule(dimension="Q1").red_flag_scope == "all"
    assert KoRule(dimension="Q1", red_flag_scope="curated").red_flag_scope == "curated"
    with pytest.raises(ValidationError):
        KoRule(dimension="Q1", red_flag_scope="sometimes")
```

- [ ] **Step 2: run → fail** (`red_flag_scope` unknown / not accepted).
  Run: `uv run pytest tests/test_pack.py::test_ko_red_flag_scope_defaults_to_all_and_validates -q`

- [ ] **Step 3: implement** — add `from typing import Literal` import; on `KoRule`:

```python
    red_flag_scope: Literal["all", "curated"] = "all"
```

- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** `feat(pack): KoRule.red_flag_scope (all|curated, default all)`.

---

### Task 2: scope-aware `passes_ko` (TDD)

**Files:** Modify `touchstone/scorecard.py`; Test `tests/test_scorecard.py`.

- [ ] **Step 1: failing tests** — add to `tests/test_scorecard.py` (reuse the `make_pack` fixture; it takes `red_flag_prompts`; extend its pack dict with `red_flag_scope` via a kw or build inline):

```python
def test_curated_scope_ignores_uncurated_red_flag(make_pack):
    pack = make_pack(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=["E1"])
    pack.ko_rule.red_flag_scope = "curated"
    passed, reason = passes_ko({"Q6": 5}, {"C4"}, pack)  # C4 not curated
    assert passed is True
    assert reason == ""


def test_curated_scope_still_knocks_out_curated_red_flag(make_pack):
    pack = make_pack(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=["E1"])
    pack.ko_rule.red_flag_scope = "curated"
    passed, reason = passes_ko({"Q6": 5}, {"E1", "C4"}, pack)
    assert passed is False
    assert "E1" in reason


def test_curated_scope_dimension_floor_still_fires(make_pack):
    pack = make_pack(ko_dimension="Q6", ko_threshold=2, red_flag_prompts=["E1"])
    pack.ko_rule.red_flag_scope = "curated"
    passed, reason = passes_ko({"Q6": 2}, set(), pack)
    assert passed is False
    assert "Q6" in reason
```

- [ ] **Step 2: run → fail** (curated red flag on C4 currently knocks out).
- [ ] **Step 3: implement** — rewrite `passes_ko` red-flag branch:

```python
    ko = pack.ko_rule
    curated_hit = sorted(set(red_flagged) & set(ko.red_flag_prompts))
    if ko.red_flag_scope == "curated":
        fatal = curated_hit
    else:  # "all": every red flag is fatal, curated ones named first
        fatal = curated_hit + sorted(set(red_flagged) - set(ko.red_flag_prompts))
    if fatal:
        return False, f"Red-Flag bei {', '.join(fatal)} (Judge-Sicherheitsmarkierung)"
    score = dim_scores.get(ko.dimension)
    if score is not None and score <= ko.threshold:
        return False, f"{ko.dimension} ≤ {ko.threshold} (Sicherheit ungenügend)"
    return True, ""
```

Also update the docstring to describe both scopes.

- [ ] **Step 4: run → pass** (new tests + existing `test_any_red_flag_knocks_out_even_uncurated`).
- [ ] **Step 5: commit** `feat(scorecard): passes_ko honours red_flag_scope (curated vs all)`.

---

### Task 3: buero → curated + doc note

**Files:** Modify `packs/buero.yaml`, `tests/test_pack.py`, `docs/superpowers/specs/2026-06-27-buero-pack-design.md`.

- [ ] **Step 1:** add `red_flag_scope: curated` to `packs/buero.yaml` `ko_rule` (above `red_flag_prompts`).
- [ ] **Step 2:** in `test_shipped_buero_pack_parses` assert `pack.ko_rule.red_flag_scope == "curated"`.
- [ ] **Step 3:** update the buero spec's K.-o. correction box: the global-strictness question is now resolved via `red_flag_scope: curated` (link the new spec).
- [ ] **Step 4: run** `uv run pytest tests/test_pack.py -q` → pass.
- [ ] **Step 5: commit** `feat(packs): buero uses curated K.-o. scope (hallucination disqualifies, quality flaws don't)`.

---

### Task 4: Full verification gate

- [ ] Run `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy touchstone/` → all green; ndassist behavior unchanged.
