"""toolbench, Mehrzug-Wertung (Pack v3).

Der Einzelzug-Lauf hat Umsicht als Fehlschlag gewertet: Ein Modell, das erst ``ls`` ruft und
dann ``pytest``, fiel durch, obwohl opencode nach dem ``ls`` weiterlaufen würde (gemessen am
2026-09-19 gegen Hetzner: alle 11 Durchfaller waren dieses Muster). Gewertet wird darum die
Summe aller Züge, und das Modell bekommt auf jeden Aufruf ein gestelltes Tool-Ergebnis zurück.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from touchstone import toolbench as tb

PACK = Path(__file__).resolve().parent.parent / "packs" / "opencode-tools.yaml"


@pytest.fixture(scope="module")
def pack() -> tb.ToolsPack:
    return tb.load_tools_pack(PACK)


def call(name: str, args: dict[str, Any] | str, index: int = 0) -> tb.ToolCall:
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return tb.ToolCall(index=index, id=f"c{index}", name=name, arguments=raw)


def turn(*calls: tb.ToolCall, content: str = "", finish: str = "tool_calls") -> tb.ToolTurn:
    return tb.ToolTurn(content=content, tool_calls=list(calls), finish_reason=finish)


def item_of(pack: tb.ToolsPack, item_id: str) -> tb.ToolItem:
    return next(i for i in pack.items if i.id == item_id)


# ------------------------------------------------------- Wertung über alle Züge (ANY-Semantik)


def test_erkundung_vor_der_zielaktion_besteht(pack: tb.ToolsPack) -> None:
    """S2: erst nachsehen, dann der richtige pytest-Aufruf — ein Bestehen, kein Durchfaller."""
    t = turn(
        call("bash", {"command": "ls /work/proj && ls /work/proj/tests"}, 0),
        call("bash", {"command": "pytest tests/test_api.py -x", "workdir": "/work/proj"}, 1),
    )
    res = tb.run_checks(item_of(pack, "S2"), t, pack.tool_schemas())
    assert [r.name for r in res if r.ok is not True] == []


def test_erkundung_ohne_zielaktion_faellt_durch(pack: tb.ToolsPack) -> None:
    """Gegenprobe: Umsicht allein reicht nicht — ohne den pytest-Aufruf bleibt es ein Fail."""
    t = turn(call("bash", {"command": "ls /work/proj && ls /work/proj/tests"}, 0))
    res = tb.run_checks(item_of(pack, "S2"), t, pack.tool_schemas())
    assert any(r.ok is False for r in res)


def test_falsche_zielaktion_faellt_trotz_erkundung_durch(pack: tb.ToolsPack) -> None:
    """Gegenprobe: der zweite Aufruf trifft die Datei, bricht aber nicht beim ersten Fehler ab."""
    t = turn(
        call("bash", {"command": "ls /work/proj"}, 0),
        call("bash", {"command": "pytest tests/test_api.py", "workdir": "/work/proj"}, 1),
    )
    res = tb.run_checks(item_of(pack, "S2"), t, pack.tool_schemas())
    failed = [r.name for r in res if r.ok is False]
    assert failed == ["bricht beim ersten Fehler ab (-x/-xq/--exitfirst/--maxfail 1)"]


def test_vorbereitender_mkdir_vor_dem_write_besteht(pack: tb.ToolsPack) -> None:
    """C1: `mkdir -p` vor dem write ist in opencode ein harmloser Zusatzschritt."""
    golden_code = (
        "import re\n\n_RX = re.compile(r'^(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?$')\n\n\n"
        "def parse_duration(s: str) -> int:\n"
        "    m = _RX.match(s.strip())\n"
        "    if not m or not s.strip():\n"
        "        raise ValueError(f'invalid duration: {s!r}')\n"
        "    h, mi, se = (int(x) if x else 0 for x in m.groups())\n"
        "    return h * 3600 + mi * 60 + se\n"
    )
    t = turn(
        call("bash", {"command": "mkdir -p /work/proj/src"}, 0),
        call("write", {"filePath": "/work/proj/src/duration.py", "content": golden_code}, 1),
    )
    res = tb.run_checks(item_of(pack, "C1"), t, pack.tool_schemas())
    assert [(r.name, r.detail) for r in res if r.ok is not True] == []


# ------------------------------------------------------- gestellte Tool-Ergebnisse


def test_read_auf_fixture_liefert_die_echte_datei(pack: tb.ToolsPack) -> None:
    item = item_of(pack, "E3")
    path = next(iter(item.fixtures))
    out = tb.tool_result_for(item, call("read", {"filePath": path}))
    assert out == tb.read_tool_output(path, item.fixtures[path])


def test_read_auf_unbekanntes_sagt_das_ehrlich(pack: tb.ToolsPack) -> None:
    out = tb.tool_result_for(item_of(pack, "S2"), call("read", {"filePath": "/nope.txt"}))
    assert "not found" in out.lower() and "/nope.txt" in out


def test_bash_bekommt_neutrale_quittung_ohne_erfundene_ausgabe(pack: tb.ToolsPack) -> None:
    """Ein Kommando mit Wirkung (kein Lesen) wird nur quittiert — der Harness erfindet keine
    Ausgabe und baut keine Shell nach."""
    out = tb.tool_result_for(item_of(pack, "S3"), call("bash", {"command": "npm run build"}))
    assert out.strip() == "(exit 0)"


def test_s2_liefert_eine_echte_auflistung(pack: tb.ToolsPack) -> None:
    """Das ausgelieferte Pack antwortet auf `ls` mit einer echten Liste (aus den Fixtures
    abgeleitet) — eine leere Quittung ließe das Modell glauben, das Projekt sei leer."""
    out = tb.tool_result_for(item_of(pack, "S2"), call("bash", {"command": "ls /work/proj"}))
    assert "pyproject.toml" in out and "tests/" in out


def test_item_darf_gezielte_antwort_hinterlegen() -> None:
    item = tb.ToolItem(
        id="X1",
        title="t",
        category="schema",
        prompt="p",
        turn_results=[tb.TurnReply(tool="bash", pattern=r"^ls ", output="src\ntests\nMakefile")],
        checks=[tb.Check(type="schema_valid")],
    )
    assert tb.tool_result_for(item, call("bash", {"command": "ls /work/proj"})) == (
        "src\ntests\nMakefile"
    )
    # ohne Treffer bleibt es bei der neutralen Quittung
    assert tb.tool_result_for(item, call("bash", {"command": "npm run build"})).strip() == (
        "(exit 0)"
    )
    # ein lesendes Kommando auf etwas Unbekanntes sagt das, statt still zu quittieren
    assert "No such file" in tb.tool_result_for(item, call("bash", {"command": "cat x"}))


def test_folgenachricht_hat_opencode_form(pack: tb.ToolsPack) -> None:
    t = turn(call("bash", {"command": "npm run build"}, 0))
    msgs = tb.follow_up_messages(item_of(pack, "S3"), t)
    assert msgs[0]["role"] == "assistant" and msgs[0]["tool_calls"][0]["function"]["name"] == "bash"
    assert msgs[1] == {"role": "tool", "tool_call_id": "c0", "content": "(exit 0)"}


# ------------------------------------------------------- Züge zusammenführen


def test_merge_turns_summiert_und_nimmt_das_letzte_ende() -> None:
    a = tb.ToolTurn(
        content="",
        reasoning="denk",
        tool_calls=[call("bash", {"command": "ls"}, 0)],
        finish_reason="tool_calls",
        prompt_tokens=10,
        completion_tokens=5,
        ttft_s=1.0,
        t_first_tool_s=1.5,
        e2e_s=2.0,
    )
    b = tb.ToolTurn(
        content="fertig",
        reasoning="mehr",
        tool_calls=[call("write", {"filePath": "/x", "content": "y"}, 0)],
        finish_reason="stop",
        prompt_tokens=30,
        completion_tokens=7,
        ttft_s=0.5,
        t_first_tool_s=0.6,
        e2e_s=3.0,
    )
    m = tb.merge_turns([a, b])
    assert [c.name for c in m.tool_calls] == ["bash", "write"]
    assert [c.index for c in m.tool_calls] == [0, 1]
    assert (m.finish_reason, m.content, m.completion_tokens) == ("stop", "fertig", 12)
    assert m.prompt_tokens == 30  # letzter Zug: enthält den ganzen Verlauf
    assert (m.ttft_s, m.t_first_tool_s, m.e2e_s) == (1.0, 1.5, 5.0)


def test_merge_turns_behaelt_fehler_des_letzten_zugs() -> None:
    a = tb.ToolTurn(tool_calls=[call("bash", {"command": "ls"}, 0)], finish_reason="tool_calls")
    b = tb.ToolTurn(error="RemoteProtocolError: weg")
    assert tb.merge_turns([a, b]).error.startswith("RemoteProtocolError")


# ------------------------------------------------------- die Schleife im Lauf


def _scripted(
    script: dict[str, list[list[tb.ToolCall]]], *, repeat_last: bool = False
) -> tuple[Any, list[list[dict[str, Any]]]]:
    """stream_fn, das je Item eine Folge von Zügen abspielt; protokolliert die gesendeten
    messages, damit der Test sieht, was das Modell im zweiten Zug vorgelegt bekam."""
    seen: list[list[dict[str, Any]]] = []
    state: dict[str, int] = {}

    def stream_fn(**kw: Any) -> Iterator[tb.ToolStreamEvent]:
        msgs = kw["messages"]
        seen.append(msgs)
        item_id = next(k for k in script if any(k in str(m.get("content")) for m in msgs))
        n = state.get(item_id, 0)
        state[item_id] = n + 1
        seq = script[item_id]
        # erschöpft: entweder den letzten Zug wiederholen oder aufhören zu rufen
        calls = seq[n] if n < len(seq) else (seq[-1] if repeat_last else [])
        for c in calls:
            yield tb.ToolStreamEvent(
                is_tool=True, tc_index=c.index, tc_id=c.id, tc_name=c.name, tc_args=c.arguments
            )
        yield tb.ToolStreamEvent(finish_reason="tool_calls" if calls else "stop")

    return stream_fn, seen


def _sub(pack: tb.ToolsPack, item_id: str) -> tb.ToolsPack:
    return pack.model_copy(update={"items": [item_of(pack, item_id)]})


def test_lauf_geht_nach_der_erkundung_weiter(pack: tb.ToolsPack, tmp_path: Path) -> None:
    """Zwei Züge: ls, dann der richtige Aufruf → bestanden, und der zweite Zug sah das
    Tool-Ergebnis des ersten."""
    marker = item_of(pack, "S2").prompt[:30]
    stream_fn, seen = _scripted(
        {
            marker: [
                [call("bash", {"command": "ls /work/proj"}, 0)],
                [call("bash", {"command": "pytest tests/test_api.py -x", "workdir": "/w"}, 0)],
            ]
        }
    )
    rows = tb.run_tools(_sub(pack, "S2"), [("m", "q", {})], stream_fn, tmp_path, log=lambda _: None)
    assert rows[0].passed is True
    assert rows[0].n_turns == 3
    assert rows[0].turn_calls == [["bash"], ["bash"], []]
    roles = [m["role"] for m in seen[1]]
    assert roles[-2:] == ["assistant", "tool"]
    assert "pyproject.toml" in seen[1][-1]["content"]


def test_schleife_endet_am_zugbudget(pack: tb.ToolsPack, tmp_path: Path) -> None:
    marker = item_of(pack, "S2").prompt[:30]
    stream_fn, seen = _scripted({marker: [[call("bash", {"command": "ls"}, 0)]]}, repeat_last=True)
    sub = _sub(pack, "S2").model_copy(update={"max_turns": 3})
    rows = tb.run_tools(sub, [("m", "q", {})], stream_fn, tmp_path, log=lambda _: None)
    assert rows[0].n_turns == 3 and len(seen) == 3
    assert rows[0].passed is False


def test_schleife_endet_wenn_das_modell_nur_noch_text_schreibt(
    pack: tb.ToolsPack, tmp_path: Path
) -> None:
    marker = item_of(pack, "S2").prompt[:30]
    stream_fn, seen = _scripted(
        {marker: [[call("bash", {"command": "pytest tests/test_api.py -x"}, 0)], []]}
    )
    rows = tb.run_tools(_sub(pack, "S2"), [("m", "q", {})], stream_fn, tmp_path, log=lambda _: None)
    assert rows[0].n_turns == 2 and len(seen) == 2


def test_transportfehler_beendet_die_schleife_sofort(pack: tb.ToolsPack, tmp_path: Path) -> None:
    calls: list[int] = []

    def stream_fn(**kw: Any) -> Iterator[tb.ToolStreamEvent]:
        calls.append(1)
        raise RuntimeError("peer closed connection")
        yield  # pragma: no cover

    rows = tb.run_tools(
        _sub(pack, "S2"),
        [("m", "q", {})],
        stream_fn,
        tmp_path,
        log=lambda _: None,
        max_consecutive_errors=0,
    )
    assert len(calls) == 1 and rows[0].error and rows[0].n_turns == 1


# ------------------------------------------------------- Pack v3 / Vergleichbarkeit


def test_pack_ist_v3_und_zaehlt_bash_nicht_mehr_exakt(pack: tb.ToolsPack) -> None:
    assert pack.version == 3
    assert pack.max_turns >= 2
    exact_bash = [
        (i.id, c.name())
        for i in pack.items
        for c in i.checks
        if c.type == "calls" and c.tool == "bash" and c.count is not None
    ]
    assert exact_bash == [], exact_bash


def test_compare_verweigert_gemischte_pack_version(pack: tb.ToolsPack) -> None:
    t = turn(call("bash", {"command": "pytest tests/test_api.py -x"}, 0))
    item = item_of(pack, "S2")
    a = tb.make_response(pack, item, "m", "q", 0, t, tb.run_checks(item, t, pack.tool_schemas()), 0)
    b = tb.make_response(pack, item, "m", "q", 0, t, tb.run_checks(item, t, pack.tool_schemas()), 0)
    object.__setattr__(b, "pack_version", 2)
    with pytest.raises(ValueError, match="pack_version"):
        tb.compare_bundles([a], [b])


# ------------------------------------------------------- Erkundung muss enden können


def test_cat_auf_bekannte_datei_liefert_sie(pack: tb.ToolsPack) -> None:
    """Der Smoke am 2026-09-20 zeigte: Wer auf `cat` nur „(exit 0)" hört, sucht weiter und
    kommt nie zur Aufgabe. Ein lesendes Kommando auf eine bekannte Datei wird darum beantwortet."""
    item = item_of(pack, "S2")
    path = "/work/proj/pyproject.toml"
    assert path in item.fixtures, "S2 muss die Dateien halten, die seine ls-Antwort nennt"
    out = tb.tool_result_for(item, call("bash", {"command": f"cat {path}"}))
    assert item.fixtures[path].strip() in out


def test_cat_auf_unbekannte_datei_sagt_das(pack: tb.ToolsPack) -> None:
    out = tb.tool_result_for(item_of(pack, "S2"), call("bash", {"command": "cat /work/proj/x.py"}))
    assert "No such file" in out


def test_nur_reine_lesekommandos_werden_beantwortet(pack: tb.ToolsPack) -> None:
    """Kein Shell-Nachbau: eine Pipeline oder ein Schreibkommando bekommt die Quittung."""
    item = item_of(pack, "S2")
    piped = tb.tool_result_for(
        item, call("bash", {"command": "cat /work/proj/pyproject.toml | wc -l"})
    )
    assert piped.strip() == tb.NEUTRAL_RESULT
    assert tb.tool_result_for(item, call("bash", {"command": "rm -rf /work/proj"})).strip() == (
        tb.NEUTRAL_RESULT
    )


def test_head_mit_zeilenzahl_wird_beantwortet(pack: tb.ToolsPack) -> None:
    out = tb.tool_result_for(
        item_of(pack, "S2"), call("bash", {"command": "head -n 5 /work/proj/tests/test_api.py"})
    )
    assert "def test_" in out


def test_jede_im_prompt_genannte_datei_ist_lesbar(pack: tb.ToolsPack) -> None:
    """Strukturwächter: Nennt ein Item-Prompt eine Datei, die das Modell lesen soll, muss sie als
    Fixture existieren. Sonst antwortet die gestellte Welt „not found", und das Modell sucht statt
    zu arbeiten (gemessen 2026-09-20 an S1 gegen Hetzner: read app.py → not found → zwei
    Such-Aufrufe → Item durchgefallen, obwohl der erste Zug genau richtig war)."""
    import re

    fehlend: list[tuple[str, str]] = []
    for item in pack.items:
        named = {p.rstrip(".,;:)") for p in re.findall(r"/work/[\w./-]+", item.prompt)}
        named = {p for p in named if "." in p.rsplit("/", 1)[-1]}  # Dateien, keine Ordner
        targets = {c.path for c in item.checks if c.path}
        for c in item.checks:
            if c.type == "call_paths" and isinstance(c.value, list):
                targets |= set(c.value)
        known = set(item.fixtures) | set(item.context_fixtures) | targets
        fehlend += [(item.id, p) for p in sorted(named) if p not in known]
    assert fehlend == [], f"im Prompt genannt, aber nicht lesbar und kein Schreibziel: {fehlend}"


# ------------------------------------------------------- Checks-Hash vs. Lauf-Parameter


def test_checks_fingerprint_ignoriert_sampling_aber_nicht_die_checks(
    pack: tb.ToolsPack, tmp_path: Path
) -> None:
    """Zwei Stufen mit ihrem je empfohlenen Sampling (Thinking vs. Instruct) sind verschiedene
    Läufe DESSELBEN Prüfstands. Der Hash, der „identische Checks" belegen soll, darf daran nicht
    scheitern — sonst ist ein Vergleich unter Hersteller-Sampling unmöglich (gemessen
    2026-09-20: tools-compare verweigerte genau das). Ändert sich ein Check, muss er abweichen."""
    base = tb.checks_fingerprint(pack)
    anders_sampling = pack.model_copy(
        update={"sampling": tb.ToolsSampling(temperature=1.0, seed=7)}
    )
    assert tb.checks_fingerprint(anders_sampling) == base

    items = [i.model_copy() for i in pack.items]
    items[0] = items[0].model_copy(update={"checks": items[0].checks[:-1]})
    assert tb.checks_fingerprint(pack.model_copy(update={"items": items})) != base

    # Das Zugbudget ist eine Messbedingung, keine Laufoption → es gehört in den Hash
    assert tb.checks_fingerprint(pack.model_copy(update={"max_turns": 9})) != base


def test_pack_fingerprint_bleibt_streng_fuer_resume(pack: tb.ToolsPack, tmp_path: Path) -> None:
    """Der Resume-Wächter bleibt am ganzen Pack: ein Sampling-Wechsel MITTEN in einem Bundle
    würde Zellen mit verschiedenen Parametern mischen."""
    import yaml

    raw = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    raw["context_dir"] = str(PACK.parent / "opencode-tools-context")  # Kopie liegt woanders
    a = tmp_path / "a.yaml"
    a.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    raw["sampling"] = {"temperature": 1.0, "seed": 42}
    b = tmp_path / "b.yaml"
    b.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    pa, pb = tb.load_tools_pack(a), tb.load_tools_pack(b)
    assert tb.pack_fingerprint(a, pa) != tb.pack_fingerprint(b, pb)


# ------------------------------------------------------- finish_reason über alle Züge


def test_finish_reason_gilt_fuer_irgendeinen_zug(pack: tb.ToolsPack) -> None:
    """M1 verlangt `finish_reason: tool_calls`. In der Schleife ist der LETZTE Zug aber der
    Abschlusszug ohne Aufruf, also `stop` — gemessen 2026-09-20 gegen Hetzner: M1 und L1 fielen
    allein daran durch, obwohl sie die Aufgabe erfüllt hatten. Erfüllt ein Zug die Erwartung,
    ist der Check erfüllt."""
    a = tb.ToolTurn(tool_calls=[call("write", {"filePath": "/x", "content": "y"}, 0)],
                    finish_reason="tool_calls")  # fmt: skip
    b = tb.ToolTurn(content="fertig", finish_reason="stop")
    merged = tb.merge_turns([a, b])
    assert merged.finish_reason == "stop"  # letzter Zug bleibt die Hauptangabe
    res = tb.run_check(
        tb.Check(type="finish_reason", value="tool_calls"), merged, pack.items[0], {}
    )
    assert res.ok is True, res.detail


def test_finish_reason_faellt_durch_wenn_kein_zug_ihn_hat(pack: tb.ToolsPack) -> None:
    """Gegenprobe: Wurde in keinem Zug so beendet, bleibt es ein Fehlschlag."""
    merged = tb.merge_turns(
        [tb.ToolTurn(finish_reason="length"), tb.ToolTurn(content="x", finish_reason="stop")]
    )
    res = tb.run_check(
        tb.Check(type="finish_reason", value="tool_calls"), merged, pack.items[0], {}
    )
    assert res.ok is False and "length" in res.detail


# ------------------------------------------------------- die Attrappe muss lesbar antworten
# Dritter Anlauf derselben Fehlerklasse (2026-09-20): xhigh verbrauchte bei C1 alle fünf Züge mit
# `ls -la`, `find`, `echo hello; whoami` und `read` auf ein Verzeichnis — Umgebungsdiagnose, weil
# jede Probe „(exit 0)" zurückgab. Gemessen wurde die Attrappe, nicht das Modell.


def test_ls_listet_die_bekannten_pfade(pack: tb.ToolsPack) -> None:
    item = item_of(pack, "C1")  # Schreibziel /work/proj/src/duration.py, keine Fixtures
    out = tb.tool_result_for(item, call("bash", {"command": "ls -la /work/proj"}))
    assert "src" in out and "No such file" not in out


def test_find_listet_rekursiv_die_bekannten_dateien(pack: tb.ToolsPack) -> None:
    out = tb.tool_result_for(
        item_of(pack, "S2"), call("bash", {"command": "find /work/proj -type f"})
    )
    assert "/work/proj/tests/test_api.py" in out and "/work/proj/pyproject.toml" in out


def test_ls_auf_unbekanntes_verzeichnis_sagt_das(pack: tb.ToolsPack) -> None:
    out = tb.tool_result_for(item_of(pack, "S2"), call("bash", {"command": "ls /nirgendwo"}))
    assert "No such file or directory" in out


def test_echo_und_pwd_antworten_wie_eine_shell(pack: tb.ToolsPack) -> None:
    """`echo hello` war die Probe, mit der das Modell prüfte, ob die Shell lebt. Eine leere
    Antwort darauf heißt für das Modell: hier ist alles kaputt."""
    item = item_of(pack, "C1")
    assert tb.tool_result_for(item, call("bash", {"command": "echo hello"})).strip() == "hello"
    assert tb.tool_result_for(item, call("bash", {"command": "pwd"})).strip() == "/work/proj"


def test_read_auf_verzeichnis_meldet_verzeichnis(pack: tb.ToolsPack) -> None:
    out = tb.tool_result_for(item_of(pack, "S2"), call("read", {"filePath": "/work/proj"}))
    assert "directory" in out.lower()


def test_schreibende_kommandos_bleiben_neutral(pack: tb.ToolsPack) -> None:
    """Gegenprobe: Es bleibt ein Stellvertreter, kein Shell-Nachbau — was Wirkung hätte, wird
    nur quittiert, und die Zielaktion bleibt der Weg über write/edit."""
    item = item_of(pack, "C1")
    for cmd in ("mkdir -p /work/proj/src", "pytest -x", "rm -rf /work", "python -c 'print(1)'"):
        assert tb.tool_result_for(item, call("bash", {"command": cmd})).strip() == tb.NEUTRAL_RESULT


def test_jedes_im_prompt_genannte_verzeichnis_existiert(pack: tb.ToolsPack) -> None:
    """Gegenstück zum Datei-Wächter: Nennt ein Prompt einen Ordner, darf `ls` darauf nicht
    „No such file" sagen — sonst hält das Modell das Projekt für kaputt und diagnostiziert die
    Umgebung, statt zu arbeiten (gemessen 2026-09-20: xhigh verbrauchte so alle fünf Züge)."""
    import re

    fehlend = []
    for item in pack.items:
        dirs = {
            d.rstrip("/")
            for d in re.findall(r"/work/[\w./-]+", item.prompt)
            if "." not in d.rsplit("/", 1)[-1]
        }
        for d in sorted(dirs):
            out = tb.tool_result_for(item, tb.ToolCall(0, "x", "bash", f'{{"command":"ls {d}"}}'))
            if "No such file" in out:
                fehlend.append((item.id, d))
    assert fehlend == [], f"im Prompt genannt, aber die gestellte Welt kennt es nicht: {fehlend}"
