# Büro-/Wissensarbeit-Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, single session). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `packs/buero.yaml` — a second eval pack (Büro-/Wissensarbeit-Assistent) next to `ndassist`, pure data, with a shipped-pack parse test.

**Architecture:** A pack is data, not code (`touchstone/pack.py` validates it). The full prompt content (25 prompts, green/red flags, dimensions, K.-o.) is fixed in the approved spec `docs/superpowers/specs/2026-06-27-buero-pack-design.md` and the adversarially-reviewed workflow design. Implementation = author the YAML + a parse/invariant test mirroring `test_shipped_ndassist_pack_parses` + doc touch-ups.

**Tech Stack:** Python 3.12, uv, pydantic (pack validation), pytest, ruff, mypy strict, PyYAML.

## Global Constraints

- `id: buero` · `title: "Büro- & Wissensarbeit-Assistent"` · `version: 1`.
- 25 prompts in 5 categories A–E (5 each); dimension weights Q1..Q7 = 3,3,2,2,2,2,1 (sum 15, `max_weighted` 75).
- `ko_rule`: `dimension: Q1`, `threshold: 2`, `red_flag_prompts: [A4, B3, C4, D1, E1, E2]`.
- `safety_critical: true` only on A4, B3, C4, D1, E1, E2; `format_strict: true` only on A3, A5, B4, C5; `repeats: 2` only on A4, B3, C4, D1, E1, E2, E4; `max_tokens: null` everywhere.
- `prompt_variants`: `baseline` (professional office-assistant system prompt) + `none` (null).
- `sampling`: temperature 0.0, seed 42.
- German prompts/flags; identifiers/keys English-keyed YAML. Source text lives inside the `prompt` string.
- All edits keep `pytest -q`, `ruff check`, `ruff format --check`, `mypy touchstone/` green.

---

### Task 1: Author `packs/buero.yaml` + shipped-pack parse test (TDD)

**Files:**
- Create: `packs/buero.yaml`
- Modify: `tests/test_pack.py` (append one test)

**Interfaces:**
- Consumes: `touchstone.pack.load_pack(path) -> Pack`; `Pack.all_prompts()`, `Pack.dimensions`, `Pack.max_weighted`, `Pack.ko_rule` (existing).
- Produces: a shipped data file at the conventional path `packs/<id>.yaml`, discoverable by the GUI glob and `bundles.pack_rel`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_pack.py`:

```python
def test_shipped_buero_pack_parses():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pack = load_pack(root / "packs" / "buero.yaml")
    # Büro brief: 25 prompts, 7 weighted dimensions summing to 15 (max 75).
    assert pack.id == "buero"
    assert len(pack.all_prompts()) == 25
    assert sum(d.weight for d in pack.dimensions) == 15
    assert pack.max_weighted == 75
    # K.-o. = no-hallucination on Q1, narrow confabulation-bait pool.
    assert pack.ko_rule.dimension == "Q1"
    assert pack.ko_rule.threshold == 2
    assert pack.ko_rule.red_flag_prompts == ["A4", "B3", "C4", "D1", "E1", "E2"]
    # Field conventions: exactly the 6 K.-o. prompts are safety_critical.
    sc = sorted(p.id for _, p in pack.all_prompts() if p.safety_critical)
    assert sc == ["A4", "B3", "C4", "D1", "E1", "E2"]
    # Every prompt has at least one green and one red flag (Judge rubric).
    assert all(p.green_flags and p.red_flags for _, p in pack.all_prompts())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pack.py::test_shipped_buero_pack_parses -q`
Expected: FAIL — file `packs/buero.yaml` does not exist (load_pack raises).

- [ ] **Step 3: Author `packs/buero.yaml`**

Write the full pack following `packs/ndassist.yaml` structure and the spec inventory. Content source = the approved, adversarially-reviewed workflow design (frame + 5 categories), with the three resolved decisions applied: narrow K.-o. pool (A4,B3,C4,D1,E1,E2 — D2/D3/D5 `safety_critical: false`), E3 = Q7 verbindliche-Zusage prompt, A3 green-flag sentence-count hardened ("Anrede/Gruß zählen nicht als Sätze; nur der Mailrumpf zählt"). Top-level keys in order: `id, title, version, description, scale, dimensions, ko_rule, prompt_variants, sampling, categories`.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/test_pack.py::test_shipped_buero_pack_parses -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packs/buero.yaml tests/test_pack.py
git commit -m "feat(packs): Büro-/Wissensarbeit eval pack (buero) + parse test"
```

---

### Task 2: Doc touch-ups (AGENTS.md + README)

**Files:**
- Modify: `AGENTS.md` (the "packs/ndassist.yaml is the first one" sentence)
- Modify: `README.md`, `README.de.md` (wherever ndassist is named as the only/example pack)

**Interfaces:** none (prose only).

- [ ] **Step 1: Update AGENTS.md** — change the qualitative-half paragraph so it names both packs (ndassist = ND-Assistent, buero = Büro-/Wissensarbeit-Assistent) instead of "the first one".

- [ ] **Step 2: Update README.md / README.de.md** — wherever the eval example uses `packs/ndassist.yaml`, add a one-line note that `packs/buero.yaml` is a second shipped pack (no command rewrite needed).

- [ ] **Step 3: Verify nothing references a removed/renamed symbol**

Run: `git grep -n "first one\|erste" AGENTS.md`
Expected: no stale "first/only pack" claim remains.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md README.de.md
git commit -m "docs: name buero pack alongside ndassist (two shipped packs)"
```

---

### Task 3: Full verification gate

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite + lint + types**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy touchstone/
```
Expected: all green; test count = previous + 1 (the new buero test). No GUI test breaks from the added pack file.

- [ ] **Step 2: (If anything red) fix inline, re-run.** Do not proceed to merge until all four are green.

---

## Self-Review

**Spec coverage:** Identity, scale, 7 dimensions, K.-o. (narrow pool), 25-prompt inventory, field conventions, E3 repurpose, A3 hardening, acceptance criteria 1–5 → all carried by Task 1 (YAML+test) + Task 2 (docs) + Task 3 (verify). Acceptance #4 (GUI lists buero, no test breaks) is covered by Task 3 Step 1.

**Placeholder scan:** Test code is concrete. The YAML body is authored in Task 1 Step 3 from the named source (spec + approved design) rather than re-pasted here — deliberate, to avoid triplicating ~400 lines of flag text; the invariant test pins the structural contract.

**Type consistency:** Uses only existing `Pack`/`load_pack` API as in the neighboring `test_shipped_ndassist_pack_parses`. No new symbols.
