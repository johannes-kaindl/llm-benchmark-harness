# tests/test_gui_app_detail.py
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_gui_compare import _two_variant_bundle, _write_compare_bundle

import touchstone.gui.app as appmod
from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry
from touchstone.judge import write_reports_jsonl
from touchstone.results import ModelReport, Verdict


class _FakeLauncher:
    def spawn(self, argv):
        return 1

    def alive(self, pid):
        return False

    def terminate(self, pid):
        return None


def _client(tmp_path):
    reg = RunRegistry(runs_dir=tmp_path, launcher=_FakeLauncher())
    return TestClient(gui_app.create_app(runs_dir=tmp_path, registry=reg))


def _mk(tmp_path):
    """Bundle with a real EvalResponse so master_rows() produces a non-empty result."""
    d = tmp_path / "2026_eval_nd"
    d.mkdir()
    (d / "bundle.json").write_text(
        json.dumps(
            {
                "pack_id": "ndassist",
                "pack_path": "packs/ndassist.yaml",
                "models": [{"id": "m", "quant": "q"}],
                "date": "2026-06-21",
                "host": {},
            }
        ),
        encoding="utf-8",
    )
    # A minimal EvalResponse so model_variant_groups() sees ("m", "none")
    resp = {
        "pack_id": "ndassist",
        "pack_version": 1,
        "machine": "t",
        "model": "m",
        "quant": "q",
        "engine": "llama.cpp",
        "engine_version": "0",
        "variant": "none",
        "category": "Sicherheit",
        "prompt_id": "E1",
        "repeat": 0,
        "response_text": "ok",
        "content_empty": False,
        "ttft_s": 0.1,
        "decode_tps": 10.0,
        "prefill_tps": 10.0,
        "e2e_s": 0.2,
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "is_cold_start": False,
        "power_source": "ac",
        "peak_rss_mb": None,
        "sys_used_mb": None,
        "mem_pressure_max": "",
        "throttled": False,
        "ok": True,
        "error": "",
        "seed": 42,
        "t_start": 0.0,
        "t_end": 0.2,
    }
    (d / "responses.jsonl").write_text(json.dumps(resp) + "\n", encoding="utf-8")
    (d / "scores.csv").write_text("metric_type\nnone\n", encoding="utf-8")
    write_reports_jsonl(
        d / "reports.jsonl",
        [ModelReport("m", "none", {"Q6": 2}, {"Q6": "schwach bei E1"})],
    )
    v = Verdict(
        model="m",
        variant="none",
        prompt_id="E1",
        repeat=0,
        category="Sicherheit",
        score=2,
        red_flag=True,
        rationale="Krise nicht erkannt",
    )
    (d / "judgements.jsonl").write_text(json.dumps(v.as_dict()) + "\n", encoding="utf-8")
    return d


def test_result_renders_scorecard_and_rationale(tmp_path):
    """Scorecard block and dim-rationale text appear in the rendered result HTML.

    The KO-branch data layer is covered by test_gui_bundle_detail.py::test_bundle_detail_ko_branches_and_cited_ids.
    """
    _mk(tmp_path)
    r = _client(tmp_path).get("/result/2026_eval_nd")
    assert r.status_code == 200
    # The Scorecard block and the Q6 rationale must be present in the rendered HTML
    assert "Scorecard" in r.text
    assert "schwach bei E1" in r.text


def test_pack_explainer_present(tmp_path):
    r = _client(tmp_path).get("/packs/packs/ndassist.yaml")
    assert r.status_code == 200
    assert "holistisch" in r.text.lower()  # the method explainer (L9)


def _mk_absolute_pack_path(tmp_path):
    """Bundle whose bundle.json carries an ABSOLUTE resolved pack_path (production shape)."""
    d = _mk(tmp_path)
    bj = d / "bundle.json"
    m = json.loads(bj.read_text(encoding="utf-8"))
    # production writes str(Path(pack_path).resolve())
    from pathlib import Path

    m["pack_path"] = str((Path.cwd() / "packs/ndassist.yaml").resolve())
    bj.write_text(json.dumps(m), encoding="utf-8")
    return d


def test_result_pack_link_works_with_absolute_pack_path(tmp_path):
    """BLOCKER: the 'Kriterien →' link must resolve even when pack_path is absolute.

    The /packs/{path} route 404s absolute paths, so result.html must link a cwd-relative
    path. We assert the rendered href and that fetching it returns 200.
    """
    import re

    _mk_absolute_pack_path(tmp_path)
    client = _client(tmp_path)
    r = client.get("/result/2026_eval_nd")
    assert r.status_code == 200
    m = re.search(r'href="(/packs/[^"]+)"', r.text)
    assert m, "no /packs/ link found in result view"
    href = m.group(1)
    assert not href.startswith("/packs//"), f"link is absolute (broken): {href}"
    linked = client.get(href)
    assert linked.status_code == 200, (
        f"pack link {href} did not resolve (status {linked.status_code})"
    )


def test_result_has_inline_method_explainer(tmp_path):
    """MAJOR 2: the result view carries the L9 method explainer inline ('holistisch')."""
    _mk(tmp_path)
    r = _client(tmp_path).get("/result/2026_eval_nd")
    assert r.status_code == 200
    assert "holistisch" in r.text.lower()


def test_result_accordion_auto_opens_on_hash(tmp_path):
    """MAJOR 3: cited #prompt-<id> anchors are wired to open the targeted accordion."""
    _mk(tmp_path)
    r = _client(tmp_path).get("/result/2026_eval_nd")
    assert r.status_code == 200
    assert 'id="prompt-E1"' in r.text  # the anchor exists
    # open-on-target wiring: x-init syncs `open` from the location hash + a hashchange listener
    assert "hashchange" in r.text
    assert "window.location.hash" in r.text
    assert "scroll-margin-top" in r.text


# ── Task 3: /result loads bundle once and passes axis comparison data ──────────


@pytest.fixture()
def multi_cell_bundle(tmp_path):
    """Judged ≥2-cell bundle (1 model × {baseline, none}) for Task 3 route tests."""
    return _two_variant_bundle(tmp_path)


@pytest.fixture()
def client(multi_cell_bundle):
    runs_dir = multi_cell_bundle.parent
    reg = RunRegistry(runs_dir=runs_dir, launcher=_FakeLauncher())
    return TestClient(appmod.create_app(runs_dir=runs_dir, registry=reg))


@pytest.fixture()
def two_model_bundle(tmp_path):
    """2-model × 2-variant judged bundle so projection-switch links (/result/…?axis=) render."""
    d = tmp_path / "2026_eval_2m"
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("alpha", "none"), ("beta", "baseline"), ("beta", "none")],
        dim_scores_by_cell={
            ("alpha", "baseline"): full,
            ("alpha", "none"): dict(full),
            ("beta", "baseline"): {q: 5 for q in full},
            ("beta", "none"): dict(full),
        },
    )
    return d


@pytest.fixture()
def two_model_client(two_model_bundle):
    runs_dir = two_model_bundle.parent
    reg = RunRegistry(runs_dir=runs_dir, launcher=_FakeLauncher())
    return TestClient(appmod.create_app(runs_dir=runs_dir, registry=reg))


def test_result_renders_headtohead_and_scatter(two_model_client, two_model_bundle):
    body = two_model_client.get(f"/result/{two_model_bundle.name}").text
    assert "Kopf-an-Kopf" in body
    assert "Effizienz-Relation" in body
    assert 'href="/result/' in body and "?axis=" in body  # axis switch points at /result
    assert "/compare/" not in body  # no dead within-bundle compare links


def test_result_route_passes_compare_detail(client, multi_cell_bundle, monkeypatch):
    """Route must load bundle once and pass compare_detail with ≥2 cells into template ctx."""
    seen: dict = {}
    real = appmod.render

    def spy_render(template, request, **ctx):
        seen.update(ctx)
        return real(template, request, **ctx)

    monkeypatch.setattr(appmod, "render", spy_render)
    resp = client.get(f"/result/{multi_cell_bundle.name}")
    assert resp.status_code == 200
    assert seen.get("compare_detail") is not None
    assert seen["compare_detail"].cells and len(seen["compare_detail"].cells) >= 2


# ── Task 6: flat master-scorecard shown only for true N×M matrices ────────────


@pytest.fixture()
def one_by_n_bundle(tmp_path):
    """1 model × 2 variants → N×1 bundle (no full_matrix)."""
    return _two_variant_bundle(tmp_path)


@pytest.fixture()
def two_by_two_bundle(tmp_path):
    """2 models × 2 variants → true 2×2 matrix (full_matrix=True)."""
    d = tmp_path / "2026_eval_2x2"
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("alpha", "none"), ("beta", "baseline"), ("beta", "none")],
        dim_scores_by_cell={
            ("alpha", "baseline"): dict(full),
            ("alpha", "none"): dict(full),
            ("beta", "baseline"): {q: 5 for q in full},
            ("beta", "none"): dict(full),
        },
    )
    return d


_SCORECARD_TITLE = '<div class="card-title">Gewichtete Master-Scorecard</div>'


# ── Task 7: client-side cell filter for the answers section ──────────────────


def test_answers_have_cell_filter_for_multi_cell(client, multi_cell_bundle):
    """≥2-cell bundle: answers section must contain Alpine filter markup and
    both cells' answers must still be present in the DOM (x-show, not removed)."""
    body = client.get(f"/result/{multi_cell_bundle.name}").text
    assert "x-data" in body and "cell" in body  # Alpine filter state present
    assert "data-cell-filter" in body  # filter control marker
    # Both cells' answers stay in the DOM (filtered via x-show, not stripped)
    assert "baseline" in body  # first cell label appears
    assert "none" in body  # second cell label appears


def test_single_cell_bundle_answers_not_hidden(tmp_path):
    """Task 7 regression: 1 model × 1 variant must NOT hide all answers.

    When compare_detail is None (no comparison), default_cell must be '__all__'
    so Alpine's x-show evaluates to true and answers are visible.
    Previously default_cell fell back to '' which hid every answer block.
    """
    d = tmp_path / "2026_eval_1x1"
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    _write_compare_bundle(
        d,
        cells=[("m", "baseline")],
        dim_scores_by_cell={("m", "baseline"): full},
    )
    client = _client(tmp_path)
    resp = client.get(f"/result/{d.name}")
    assert resp.status_code == 200
    body = resp.text
    # The Alpine state must default to __all__ so x-show is true for every answer block
    assert "x-data=\"{ cell: '__all__' }\"" in body, (
        "default_cell must be '__all__' for a single-cell bundle so answers are visible"
    )
    # At least one answer block (data-cell= attribute) must be present
    assert "data-cell=" in body, "answer blocks with data-cell= must be rendered (not stripped)"


# ── Task 6: flat master-scorecard shown only for true N×M matrices ────────────


def test_flat_scorecard_only_for_full_matrix(one_by_n_bundle, two_by_two_bundle):
    """1×N suppresses flat master-scorecard card; 2×2 keeps it.

    The string "Gewichtete Master-Scorecard" also appears in the method explainer,
    so we match on the card-title div which is unique to the scorecard card.
    """
    # 1×N: head-to-head present, flat master-scorecard card suppressed
    c1 = _client(one_by_n_bundle.parent)
    b1 = c1.get(f"/result/{one_by_n_bundle.name}").text
    assert "Kopf-an-Kopf" in b1
    assert _SCORECARD_TITLE not in b1
    # 2×2: both head-to-head and master-scorecard card present
    c2 = _client(two_by_two_bundle.parent)
    b2 = c2.get(f"/result/{two_by_two_bundle.name}").text
    assert "Kopf-an-Kopf" in b2
    assert _SCORECARD_TITLE in b2


# ── composite answer cell-filter (N×M fix) ────────────────────────────────────


@pytest.fixture()
def two_by_two_bundle_filter(tmp_path):
    """True 2×2 bundle (alpha/beta × baseline/none) for filter coherence tests."""
    d = tmp_path / "2026_eval_filter2x2"
    full = {q: 4 for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]}
    _write_compare_bundle(
        d,
        cells=[("alpha", "baseline"), ("alpha", "none"), ("beta", "baseline"), ("beta", "none")],
        dim_scores_by_cell={
            ("alpha", "baseline"): full,
            ("alpha", "none"): dict(full),
            ("beta", "baseline"): {q: 5 for q in full},
            ("beta", "none"): dict(full),
        },
    )
    return d


def test_2x2_filter_all_composite_keys_in_buttons_and_data_cells(two_by_two_bundle_filter):
    """On a 2×2 result page every composite model|variant key appears both in a
    filter button and in a data-cell attribute.  Axis is irrelevant — the fix is
    axis-independent (composite keys, not axis-projected labels)."""
    import re

    runs_dir = two_by_two_bundle_filter.parent
    reg = RunRegistry(runs_dir=runs_dir, launcher=_FakeLauncher())
    c = TestClient(appmod.create_app(runs_dir=runs_dir, registry=reg))
    body = c.get(f"/result/{two_by_two_bundle_filter.name}").text

    expected_keys = {"alpha|baseline", "alpha|none", "beta|baseline", "beta|none"}

    # Every key must appear in a @click="cell='…'" button
    button_keys = set(re.findall(r"@click=\"cell='([^']+)'\"", body))
    for key in expected_keys:
        assert key in button_keys, f"composite key {key!r} missing from filter buttons"

    # Every key must appear in a data-cell="…" attribute
    datacell_keys = set(re.findall(r'data-cell="([^"]+)"', body))
    for key in expected_keys:
        assert key in datacell_keys, f"composite key {key!r} missing from data-cell attrs"


def test_1x2_filter_labels_are_variant_names(client, multi_cell_bundle):
    """On a 1×2 bundle, filter button labels are variant names (no model prefix).
    Composite keys (m|baseline, m|none) still wire buttons to data-cell attrs correctly."""
    import re

    body = client.get(f"/result/{multi_cell_bundle.name}").text

    # Extract ALL filter button labels (buttons with @click="cell='...'">LABEL</button>).
    # The pattern captures the visible label between > and </button>.
    all_button_labels = re.findall(r"""@click="cell='[^']+'"[^>]*>\s*([^<]+?)\s*</button>""", body)
    # Exclude the "alle" control button — only keep per-cell filter buttons.
    cell_button_labels = [lbl for lbl in all_button_labels if lbl.lower() != "alle"]

    # 1×2 bundle has one model ("m") and two variants, so labels must be variant names only —
    # never "m · baseline" style composites and never the model name as prefix.
    assert len(cell_button_labels) >= 2, (
        f"Expected at least 2 cell filter buttons, got: {cell_button_labels}"
    )
    assert all(" · " not in lbl for lbl in cell_button_labels), (
        f"1×2 filter buttons must show variant names only, not composites — got: {cell_button_labels}"
    )
    assert all(not lbl.lower().startswith("m ") for lbl in cell_button_labels), (
        f"1×2 filter button labels must not start with the model name — got: {cell_button_labels}"
    )
    assert set(cell_button_labels) == {"baseline", "none"}, (
        f"Expected variant names as button labels, got: {cell_button_labels}"
    )

    # Composite keys must still appear in data-cell attributes (wiring is correct).
    datacell_keys = set(re.findall(r'data-cell="([^"]+)"', body))
    assert "m|baseline" in datacell_keys or any("|baseline" in k for k in datacell_keys)
    assert "m|none" in datacell_keys or any("|none" in k for k in datacell_keys)
