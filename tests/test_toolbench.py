"""toolbench: stream assembly, schema/edit/code checks, orchestration, paired compare.

The shipped pack is validated against a hand-written GOLDEN answer per item (every check must
pass) and against deliberately broken answers (the targeted check must fail) — so each check is
shown to be able to fail (CORE-TEST-01), not just to say green.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from touchstone import toolbench as tb

PACK = Path(__file__).resolve().parent.parent / "packs" / "opencode-tools.yaml"


def call(name: str, args: dict[str, Any] | str, index: int = 0) -> tb.ToolCall:
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return tb.ToolCall(index=index, id=f"c{index}", name=name, arguments=raw)


def turn(*calls: tb.ToolCall, content: str = "", finish: str = "tool_calls") -> tb.ToolTurn:
    return tb.ToolTurn(content=content, tool_calls=list(calls), finish_reason=finish)


def writes(*pairs: tuple[str, str]) -> tb.ToolTurn:
    return turn(
        *[call("write", {"filePath": p, "content": c}, i) for i, (p, c) in enumerate(pairs)]
    )


def edit(path: str, old: str, new: str, index: int = 0, **kw: Any) -> tb.ToolCall:
    return call("edit", {"filePath": path, "oldString": old, "newString": new, **kw}, index)


# --------------------------------------------------------------------------- golden answers

HTML = "<!doctype html><html><head><style>body{{margin:0}}</style></head><body>{}</body></html>"

GOLDEN: dict[str, tb.ToolTurn] = {
    "S1": turn(call("read", {"filePath": "/work/proj/src/app.py", "limit": 40})),
    "S2": turn(call("bash", {"command": "pytest tests/test_api.py -x", "workdir": "/work/proj"})),
    "S3": turn(
        call("bash", {"command": "npm run build", "workdir": "/work/site", "timeout": 120000})
    ),
    "S4": writes(
        (
            "/work/proj/config.json",
            json.dumps(
                {
                    "name": "touchstone-demo",
                    "version": "0.3.1",
                    "debug": False,
                    "ports": [8080, 8443],
                    "pattern": r"^\d{3}-[a-z]+$",
                    "greeting": 'Er sagte "Grüß Gott" und ging.',
                    "windows_path": "C:\\data\\in",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    ),
    "S5": writes(
        (
            "/work/proj/Makefile",
            ".PHONY: test lint\n\ntest:\n\tuv run pytest -q\n\nlint:\n\tuv run ruff check .\n",
        )
    ),
    "S6": turn(
        content="merge erzeugt einen Merge-Commit, rebase schreibt die Historie um.", finish="stop"
    ),
    "S7": turn(edit("/work/proj/src/stats.py", "tmp_val", "running_total", replaceAll=True)),
    "M1": writes(
        ("/work/video/frames/01-titel.html", HTML.format("<h1>Außenposten</h1><p>Sub</p>")),
        (
            "/work/video/frames/02-karte.html",
            HTML.format(
                '<svg viewBox="0 0 1920 1080">'
                + "".join(f'<circle cx="{i * 100}" cy="50" r="9"/>' for i in range(5))
                + '<path d="M0 0 L100 100"/></svg>'
            ),
        ),
        (
            "/work/video/frames/03-abspann.html",
            HTML.format("<ul><li>A</li><li>B</li><li>C</li></ul>"),
        ),
    ),
    "M2": turn(
        call("read", {"filePath": "/work/proj/README.md"}, 0),
        call("read", {"filePath": "/work/proj/pyproject.toml"}, 1),
    ),
    "M3": writes(
        (
            "/work/proj/src/textutil.py",
            "import re\n\n"
            "def slugify(text: str) -> str:\n"
            "    t = text.lower()\n"
            "    for a, b in (('ä','ae'),('ö','oe'),('ü','ue'),('ß','ss')):\n"
            "        t = t.replace(a, b)\n"
            "    t = re.sub(r'[^a-z0-9]+', '-', t)\n"
            "    return t.strip('-')\n",
        ),
        (
            "/work/proj/tests/test_textutil.py",
            "from textutil import slugify\n\ndef test_x():\n    assert slugify('A') == 'a'\n",
        ),
    ),
    "M4": writes(
        (
            "/work/video/intro/index.html",
            '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
            '<script src="main.js"></script></head><body><div id="stage">'
            '<div class="clip"></div><div class="clip"></div><div class="clip"></div>'
            "</div></body></html>",
        ),
        (
            "/work/video/intro/style.css",
            "#stage { width: 1920px; height: 1080px; position: relative; }\n"
            ".clip { position: absolute; }\n",
        ),
        (
            "/work/video/intro/main.js",
            "document.addEventListener('DOMContentLoaded', () => {\n"
            "  const clips = document.querySelectorAll('.clip');\n"
            "  clips.forEach((c, i) => c.setAttribute('data-start', String(i * 2)));\n"
            "  console.log(clips.length);\n});\n",
        ),
    ),
    "C1": writes(
        (
            "/work/proj/src/duration.py",
            "import re\n\n"
            "def parse_duration(s: str) -> int:\n"
            "    m = re.fullmatch(r'(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?', s)\n"
            "    if not s or not m:\n"
            "        raise ValueError(s)\n"
            "    h, mi, se = (int(x) if x else 0 for x in m.groups())\n"
            "    return h * 3600 + mi * 60 + se\n",
        )
    ),
    "C2": writes(
        (
            "/work/proj/src/intervals.py",
            "def merge_intervals(iv):\n"
            "    out = []\n"
            "    for a, b in sorted(iv):\n"
            "        if out and a <= out[-1][1]:\n"
            "            out[-1] = (out[-1][0], max(out[-1][1], b))\n"
            "        else:\n"
            "            out.append((a, b))\n"
            "    return out\n",
        )
    ),
    "C3": writes(
        (
            "/work/proj/src/iban.py",
            "import re\n\n"
            "def iban_valid(iban: str) -> bool:\n"
            "    s = iban.replace(' ', '').upper()\n"
            "    if not re.fullmatch(r'[A-Z0-9]{15,34}', s):\n"
            "        return False\n"
            "    r = s[4:] + s[:4]\n"
            "    n = ''.join(str(int(ch, 36)) for ch in r)\n"
            "    return int(n) % 97 == 1\n",
        )
    ),
    "C4": writes(
        (
            "/work/video/frames/balken.html",
            HTML.format(
                '<svg viewBox="0 0 400 200">'
                + "".join(
                    f'<rect x="{i * 100}" y="{200 - v * 20}" width="80" height="{v * 20}"/>'
                    f'<text x="{i * 100}" y="195">{v}</text>'
                    for i, v in enumerate([3, 7, 5, 9])
                )
                + "</svg>"
            ),
        )
    ),
    "C5": writes(
        (
            "/work/video/lib/timing.js",
            "function framesFor(seconds, fps) { return Math.round(seconds * fps); }\n"
            "function toTimecode(frame, fps) {\n"
            "  const p = (n) => String(n).padStart(2, '0');\n"
            "  const s = Math.floor(frame / fps);\n"
            "  return `${p(Math.floor(s / 60))}:${p(s % 60)}:${p(frame % fps)}`;\n}\n"
            "module.exports = { framesFor, toTimecode };\n",
        )
    ),
    "E1": turn(edit("/work/proj/src/settings.py", "MAX_RETRIES = 3", "MAX_RETRIES = 5")),
    "E2": turn(
        edit("/work/proj/src/window.py", "range(len(values) - k)", "range(len(values) - k + 1)")
    ),
    "E3": turn(
        edit(
            "/work/proj/src/users.py",
            '        if u["email"] == email:\n            return u\n    return None',
            '        if u["email"] == email:\n            return u\n'
            '    raise LookupError(f"no user {email}")',
        )
    ),
    "E4": turn(
        edit(
            "/work/video/frames/01-titel.html",
            "font-size: 120px;\n      color: #333;",
            "font-size: 120px;\n      color: #e4572e;",
        )
    ),
    "E5": turn(
        edit(
            "/work/proj/src/importer.py",
            "        for row in rows:\n",
            '        for row in rows:\n            if row["id"] is None or row["id"] == "":\n'
            "                continue\n",
        )
    ),
}


GOLDEN["L1"], GOLDEN["L2"], GOLDEN["L3"] = GOLDEN["M1"], GOLDEN["E3"], GOLDEN["C1"]


@pytest.fixture(scope="module")
def pack() -> tb.ToolsPack:
    return tb.load_tools_pack(PACK)


def test_shipped_pack_validates(pack: tb.ToolsPack) -> None:
    assert 15 <= len(pack.items) <= 25
    assert pack.max_tokens == 32000
    assert set(pack.tool_schemas()) == {"read", "write", "edit", "bash"}
    assert {i.category for i in pack.items} == {"schema", "multi", "code", "edit", "longctx"}


def test_longctx_items_mirror_their_originals_and_carry_context(pack: tb.ToolsPack) -> None:
    by = {i.id: i for i in pack.items}
    for long_id, orig_id in (("L1", "M1"), ("L2", "E3"), ("L3", "C1")):
        lg, og = by[long_id], by[orig_id]
        assert (lg.prompt, lg.checks, lg.fixtures) == (og.prompt, og.checks, og.fixtures)
        assert len(lg.context_fixtures) == 12 and not og.context_fixtures
        msgs = tb.build_messages(pack, lg)
        chars = sum(len(m.get("content") or "") for m in msgs)
        # ~50k tokens of context: big enough to matter, small enough for the 8bit JIT (131k)
        assert 150_000 < chars < 300_000
        # context exchange first, then the task (then E3's own preread of the target file)
        roles = [m["role"] for m in msgs]
        assert roles[:3] == ["system", "user", "assistant"]
        assert msgs[1]["content"] == pack.context_intro
        task_at = next(i for i, m in enumerate(msgs) if m.get("content") == lg.prompt)
        assert msgs[task_at - 1] == {"role": "assistant", "content": pack.context_ack}
        assert all(m["role"] == "tool" for m in msgs[3 : task_at - 1])


def test_missing_context_file_is_a_load_error(tmp_path: Path) -> None:
    import yaml

    raw = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    raw["context_dir"] = "nope"
    bad = tmp_path / "p.yaml"
    bad.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="context file missing"):
        tb.load_tools_pack(bad)


def test_golden_covers_every_item(pack: tb.ToolsPack) -> None:
    assert set(GOLDEN) == {i.id for i in pack.items}


@pytest.mark.parametrize("item_id", sorted(GOLDEN))
def test_golden_answer_passes_every_check(pack: tb.ToolsPack, item_id: str) -> None:
    item = next(i for i in pack.items if i.id == item_id)
    res = tb.run_checks(item, GOLDEN[item_id], pack.tool_schemas())
    bad = [(r.name, r.detail) for r in res if r.ok is not True]
    assert not bad, bad


# --------------------------------------------------------------------------- broken answers
# (item, broken turn, name of a check that MUST fail)

BROKEN: list[tuple[str, tb.ToolTurn, str]] = [
    (
        "S1",
        turn(call("read", {"filePath": "/work/proj/src/app.py", "limit": "40"})),
        "schema_valid",
    ),
    (
        "S1",
        turn(call("read", {"filePath": "/work/proj/src/app.py", "limit": "40"})),
        "arg_equals:read:limit",
    ),
    (
        "S2",
        turn(call("bash", {"command": "pytest -x tests/test_api.py", "description": "run"})),
        "schema_valid",
    ),
    ("S2", turn(call("bash", {"command": "pytest tests/test_api.py"})), "arg_regex:bash:command"),
    (
        "S3",
        turn(call("bash", {"command": "npm run build", "timeout": 120})),
        "arg_equals:bash:timeout",
    ),
    (
        "S4",
        writes(("/work/proj/config.json", '{"pattern": "^d{3}-[a-z]+$"}')),
        "write_json_equals:/work/proj/config.json",
    ),
    (
        "S5",
        writes(
            (
                "/work/proj/Makefile",
                ".PHONY: test lint\ntest:\n    uv run pytest -q\nlint:\n    uv run ruff check .\n",
            )
        ),
        "test-Rezept mit Tab",
    ),
    ("S6", turn(call("bash", {"command": "git help rebase"})), "no_tools"),
    (
        "S7",
        turn(edit("/work/proj/src/stats.py", "tmp_val", "running_total")),
        "edit_applies:/work/proj/src/stats.py",
    ),
    (
        "S7",
        turn(edit("/work/proj/src/stats.py", "tmp_val", "running_total", replaceAll="true")),
        "schema_valid",
    ),
    (
        "M1",
        turn(*GOLDEN["M1"].tool_calls[:2], call("write", "", 2), finish="length"),
        "no_empty_args",
    ),
    (
        "M1",
        turn(*GOLDEN["M1"].tool_calls[:2], call("write", "", 2), finish="length"),
        "finish_reason",
    ),
    (
        "M1",
        turn(*GOLDEN["M1"].tool_calls[:2], call("write", "", 2), finish="length"),
        "calls:write",
    ),
    (
        "M1",
        turn(*GOLDEN["M1"].tool_calls[:2], call("write", "", 2), finish="length"),
        "schema_valid",
    ),
    ("M2", turn(call("read", {"filePath": "/work/proj/README.md"})), "call_paths:read"),
    (
        "M3",
        writes(
            ("/work/proj/src/textutil.py", "def slugify(t):\n    return t.lower()\n"),
            ("/work/proj/tests/test_textutil.py", "def test_x(): pass\n"),
        ),
        "write_python:/work/proj/src/textutil.py",
    ),
    (
        "M4",
        writes(
            *[
                (json.loads(c.arguments)["filePath"], json.loads(c.arguments)["content"])
                for c in GOLDEN["M4"].tool_calls[:2]
            ],
            (
                "/work/video/intro/main.js",
                "document.addEventListener('DOMContentLoaded', () => { console.log(",
            ),
        ),
        "write_js_syntax:/work/video/intro/main.js",
    ),
    (
        "C1",
        writes(("/work/proj/src/duration.py", "def parse_duration(s):\n    return 0\n")),
        "write_python:/work/proj/src/duration.py",
    ),
    (
        "C4",
        writes(
            (
                "/work/video/frames/balken.html",
                GOLDEN["C4"].tool_calls[0].arguments
                and json.loads(GOLDEN["C4"].tool_calls[0].arguments)["content"].replace(
                    'height="60"', 'height="3"'
                ),
            )
        ),
        "rect-Höhen und y korrekt",
    ),
    (
        "C5",
        writes(
            (
                "/work/video/lib/timing.js",
                "module.exports = { framesFor: (s, f) => s * f, toTimecode: () => '' };",
            )
        ),
        "write_node:/work/video/lib/timing.js",
    ),
    (
        "E1",
        turn(edit("/work/proj/src/settings.py", "3: MAX_RETRIES = 3", "3: MAX_RETRIES = 5")),
        "edit_applies:/work/proj/src/settings.py",
    ),
    (
        "E3",
        turn(edit("/work/proj/src/users.py", "    return None", '    raise LookupError("x")')),
        "edit_applies:/work/proj/src/users.py",
    ),
    (
        "E4",
        turn(
            edit(
                "/work/video/frames/01-titel.html",
                "color: #333;",
                "color: #e4572e;",
                replaceAll=True,
            )
        ),
        "p unverändert",
    ),
    (
        "E5",
        turn(
            edit(
                "/work/proj/src/importer.py",
                "for row in rows:\n",
                'for row in rows:\nif not row["id"]:\n    continue\n',
            )
        ),
        "edited_python:/work/proj/src/importer.py",
    ),
]


@pytest.mark.parametrize(("item_id", "broken", "must_fail"), BROKEN)
def test_broken_answer_fails_targeted_check(
    pack: tb.ToolsPack, item_id: str, broken: tb.ToolTurn, must_fail: str
) -> None:
    item = next(i for i in pack.items if i.id == item_id)
    res = {r.name: r for r in tb.run_checks(item, broken, pack.tool_schemas())}
    assert must_fail in res, sorted(res)
    assert res[must_fail].ok is False, res[must_fail]


# --------------------------------------------------------------------------- unit


def test_collect_turn_assembles_fragments_and_keeps_truncated_empty_call() -> None:
    evs = [
        tb.ToolStreamEvent(reasoning="thinking…"),
        tb.ToolStreamEvent(tc_index=0, tc_id="a", tc_name="write", tc_args='{"filePath": "/x", '),
        tb.ToolStreamEvent(tc_index=0, tc_args='"content": "hi"}'),
        tb.ToolStreamEvent(tc_index=1, tc_id="b", tc_name="write", tc_args=""),
        tb.ToolStreamEvent(finish_reason="length"),
        tb.ToolStreamEvent(prompt_tokens=10, completion_tokens=99),
    ]
    ticks = iter(range(100))
    t = tb.collect_turn(evs, clock=lambda: float(next(ticks)))
    assert [c.name for c in t.tool_calls] == ["write", "write"]
    assert json.loads(t.tool_calls[0].arguments) == {"filePath": "/x", "content": "hi"}
    assert t.tool_calls[1].arguments == ""
    assert t.finish_reason == "length" and t.completion_tokens == 99
    assert t.reasoning == "thinking…" and t.ttft_s is not None and t.t_first_tool_s is not None


def test_collect_turn_keeps_partial_on_stream_error() -> None:
    def gen() -> Iterator[tb.ToolStreamEvent]:
        yield tb.ToolStreamEvent(content="a")
        raise ConnectionError("dropped")

    t = tb.collect_turn(gen(), clock=lambda: 0.0)
    assert t.content == "a" and "ConnectionError" in t.error


def test_validate_args() -> None:
    schema = {
        "properties": {"a": {"type": "integer"}, "b": {"type": "boolean"}, "c": {"enum": ["x"]}},
        "required": ["a"],
    }
    assert tb.validate_args({"a": 1}, schema) == []
    assert tb.validate_args({"a": 1.0}, schema) == []  # integral float is a JSON integer
    assert tb.validate_args({"a": True}, schema)  # bool is not an integer in JSON
    assert tb.validate_args({"b": "true"}, schema)  # missing a + wrong type
    assert tb.validate_args({"a": 1, "zz": 1}, schema) == ["unknown key 'zz'"]
    assert tb.validate_args({"a": 1, "c": "y"}, schema) == ["'c': 'y' not in enum"]
    assert tb.validate_args([1], schema)


def test_apply_edits_semantics() -> None:
    orig = "a\nb\na\n"
    ok, err = tb.apply_edits(turn(edit("/f", "a", "z", replaceAll=True)), "/f", orig)
    assert ok == "z\nb\nz\n" and err == ""
    assert tb.apply_edits(turn(edit("/f", "a", "z")), "/f", orig)[0] is None  # ambiguous
    assert tb.apply_edits(turn(edit("/f", "q", "z")), "/f", orig)[0] is None  # not found
    assert tb.apply_edits(turn(edit("/f", "b", "b")), "/f", orig)[0] is None  # no-op edit
    seq = turn(edit("/f", "b", "B", 0), edit("/f", "B\na", "B\nA", 1))
    assert tb.apply_edits(seq, "/f", orig)[0] == "a\nB\nA\n"  # applied in order


def test_read_tool_output_matches_opencode_format() -> None:
    out = tb.read_tool_output("/p", "x\ny\n")
    assert out.startswith("<path>/p</path>\n<type>file</type>\n<content>\n1: x\n2: y\n")
    assert out.endswith("(End of file - total 2 lines)\n</content>")


def test_build_messages_prereads_as_tool_results(pack: tb.ToolsPack) -> None:
    item = next(i for i in pack.items if i.id == "E1")
    msgs = tb.build_messages(pack, item)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "read"
    assert "3: MAX_RETRIES = 3" in msgs[3]["content"]


def test_item_validation_rejects_edit_check_without_fixture() -> None:
    with pytest.raises(ValueError, match="no fixture"):
        tb.ToolItem(
            id="x", title="t", category="edit", prompt="p",
            checks=[tb.Check(type="edit_applies", path="/nope")],
        )  # fmt: skip


def test_not_measured_is_not_a_pass(pack: tb.ToolsPack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tb.shutil, "which", lambda _: None)
    item = next(i for i in pack.items if i.id == "C5")
    res = tb.run_checks(item, GOLDEN["C5"], pack.tool_schemas())
    node = next(r for r in res if r.name.startswith("write_node"))
    assert node.ok is None
    resp = tb.make_response(pack, item, "m", "q", 0, GOLDEN["C5"], res, 0.0)
    assert resp.checks_measured == len(res) - 1


def test_run_tools_incremental_resume_and_compare(pack: tb.ToolsPack, tmp_path: Path) -> None:
    sub = tb.ToolsPack.model_validate(
        {**pack.model_dump(), "items": [i.model_dump() for i in pack.items if i.id in ("S1", "S6")]}
    )
    seen: list[str] = []

    def fake_stream(**kw: Any) -> Iterator[tb.ToolStreamEvent]:
        seen.append(kw["model"])
        assert kw["tools"] == sub.tools and kw["max_tokens"] == 32000
        if "Zeilen" in kw["messages"][1]["content"]:
            good = kw["model"] == "good"
            args = {"filePath": "/work/proj/src/app.py", "limit": 40 if good else "40"}
            yield tb.ToolStreamEvent(
                tc_index=0, tc_id="x", tc_name="read", tc_args=json.dumps(args)
            )
            yield tb.ToolStreamEvent(finish_reason="tool_calls")
        else:
            yield tb.ToolStreamEvent(content="Antwort")
            yield tb.ToolStreamEvent(finish_reason="stop")

    a = tb.run_tools(sub, [("good", "8bit", {})], fake_stream, tmp_path / "a", log=lambda _: None)
    b = tb.run_tools(sub, [("bad", "4bit", {})], fake_stream, tmp_path / "b", log=lambda _: None)
    assert [r.passed for r in a] == [True, True]
    assert [r.passed for r in b] == [False, True]
    n = len(seen)
    again = tb.run_tools(sub, [("good", "8bit", {})], fake_stream, tmp_path / "a", resume=True,
                         log=lambda _: None)  # fmt: skip
    assert len(seen) == n and len(again) == 2  # nothing re-run
    cmp = tb.compare_bundles(
        tb.load_tool_responses(tmp_path / "b" / "responses.jsonl"),
        tb.load_tool_responses(tmp_path / "a" / "responses.jsonl"),
    )
    assert (cmp.pairs, cmp.only_a, cmp.only_b, cmp.both_pass) == (2, 0, 1, 1)
    assert cmp.label_a == "4bit" and "McNemar" in tb.render_compare_md(cmp)
    tb.write_results_csv(a, tmp_path / "r.csv")
    assert "Items bestanden" in tb.render_report_md(sub, a + b, {"x": 1})


def test_mcnemar_exact() -> None:
    assert tb.mcnemar_exact_p(0, 0) == 1.0
    assert tb.mcnemar_exact_p(0, 6) == pytest.approx(2 / 64)
    assert tb.mcnemar_exact_p(3, 3) == 1.0


def test_request_error_is_recorded_not_raised(pack: tb.ToolsPack, tmp_path: Path) -> None:
    sub = tb.ToolsPack.model_validate({**pack.model_dump(), "items": [pack.items[0].model_dump()]})

    def boom(**kw: Any) -> Iterator[tb.ToolStreamEvent]:
        raise RuntimeError("Model unloaded.")

    rows = tb.run_tools(sub, [("m", "q", {})], boom, tmp_path, log=lambda _: None)
    assert rows[0].passed is False and "Model unloaded" in rows[0].error


# --------------------------------------------------------------------------- real SDK over SSE


def test_client_stream_tools_over_real_sdk_sse() -> None:
    """client.stream_tools through the real OpenAI SDK against a local SSE server that streams
    like LM Studio: reasoning, one complete write split over chunks, one truncated write with a
    name but empty arguments, finish_reason=length, usage. (A stand-in server — it pins the
    parsing contract, it does not prove LM Studio's behaviour.)"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from touchstone.client import OpenAIStreamClient

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }  # fmt: skip

    fn0 = {"name": "write", "arguments": '{"filePath": "/a", '}
    chunks = [
        chunk({"role": "assistant", "reasoning_content": "hmm"}),
        chunk({"tool_calls": [{"index": 0, "id": "c0", "type": "function", "function": fn0}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"content": "x"}'}}]}),
        chunk(
            {
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "write", "arguments": ""},
                    }
                ]
            }
        ),
        chunk({}, finish="length"),
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        },
    ]
    got: dict[str, Any] = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a: Any) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client = OpenAIStreamClient(f"http://127.0.0.1:{srv.server_port}/v1", max_retries=0)
        tools: list[dict[str, object]] = [{"type": "function", "function": {"name": "write"}}]
        t = tb.collect_turn(
            client.stream_tools(messages=[{"role": "user", "content": "hi"}], model="m",
                                tools=tools, max_tokens=32000, temperature=0.0, seed=42),
            clock=lambda: 0.0,
        )  # fmt: skip
    finally:
        srv.shutdown()
    assert got["body"]["tools"] == tools and got["body"]["max_tokens"] == 32000
    assert [c.arguments for c in t.tool_calls] == ['{"filePath": "/a", "content": "x"}', ""]
    assert t.finish_reason == "length" and t.completion_tokens == 7 and t.reasoning == "hmm"
