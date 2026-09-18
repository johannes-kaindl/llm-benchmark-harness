"""Deterministic tool-calling / code pack ("tools pack").

The qualitative ``eval`` scores free text with an LLM judge. Structured output — tool choice,
JSON arguments, schema conformance, code that has to run — can be checked mechanically, and a
judge would only add noise there. This module is that mechanical half:

    ToolsPack (YAML)  → items: prompt (+ pre-read fixture files) + deterministic checks
    collect_turn()    → folds the client's raw ToolStreamEvents into one ToolTurn (pure)
    run_checks()      → ToolTurn × item → CheckResults (pure, except code execution in a subprocess)
    run_tools()       → matrix model × item × repeat → responses.jsonl (incremental, resumable)
    compare_bundles() → paired A/B per item: discordant counts + exact McNemar p (pure)

Tool schemas live in the pack (data), mirrored from opencode so a failure here is a failure
there. The only engine-aware code stays in ``client.py`` (``stream_tools``).
"""

from __future__ import annotations

import csv
import html.parser
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------------------------------- pack schema

CheckType = Literal[
    "calls",
    "no_tools",
    "schema_valid",
    "no_empty_args",
    "finish_reason",
    "arg_equals",
    "arg_regex",
    "write_regex",
    "write_json_equals",
    "write_python",
    "write_html",
    "write_js_syntax",
    "write_check",
    "write_node",
    "call_paths",
    "edit_applies",
    "edited_python",
    "edited_regex",
]


class Check(BaseModel):
    type: CheckType
    tool: str | None = None  # calls/arg_*: restrict to this tool
    count: int | None = None  # calls: exact
    min: int | None = None  # calls: at least
    max: int | None = None  # calls: at most
    key: str | None = None  # arg_*: argument key
    value: Any = None  # arg_equals / write_json_equals / finish_reason / call_paths (list)
    pattern: str | None = None  # *_regex
    absent: bool = False  # *_regex: pattern must NOT match
    path: str | None = None  # write_* / edit*: target filePath
    test: str | None = None  # *_python/_node: asserts run after the file's code;
    # write_check: python asserts with the written file bound to CONTENT
    tags: dict[str, int] = Field(default_factory=dict)  # write_html: tag → min count
    label: str = ""  # optional human label for the report

    def name(self) -> str:
        if self.label:
            return self.label
        bits: list[str] = [self.type]
        for v in (self.tool, self.key, self.path):
            if v:
                bits.append(str(v))
        return ":".join(bits)


class ToolItem(BaseModel):
    id: str
    title: str
    category: str
    prompt: str
    tests: str = ""  # what the item probes (documentation)
    fixtures: dict[str, str] = Field(default_factory=dict)  # filePath → original content
    preread: list[str] = Field(default_factory=list)  # fixtures fed as prior read tool results
    # Long-context items: files (names inside the pack's context_dir) read in an EARLIER exchange,
    # before the task — reproduces a running opencode session with a large, realistic context.
    context: list[str] = Field(default_factory=list)
    context_fixtures: dict[str, str] = Field(default_factory=dict)  # filled by load_tools_pack
    max_tokens: int | None = None  # None → pack default
    repeats: int = 1
    checks: list[Check]

    @model_validator(mode="after")
    def _preread_known(self) -> ToolItem:
        missing = [p for p in self.preread if p not in self.fixtures]
        if missing:
            raise ValueError(f"item {self.id}: preread paths without fixture: {missing}")
        for c in self.checks:
            if c.type in ("edit_applies", "edited_python", "edited_regex") and (
                c.path not in self.fixtures
            ):
                raise ValueError(f"item {self.id}: {c.type} path {c.path!r} has no fixture")
        return self


class ToolsSampling(BaseModel):
    temperature: float = 0.0
    seed: int = 42


class ToolsPack(BaseModel):
    id: str
    title: str
    version: int = 1
    description: str = ""
    source: str = ""  # where the tool schemas were taken from
    context_dir: str | None = None  # relative to the pack file; holds long-context snapshots
    context_root: str = "/work/proj/src/touchstone"  # virtual dir the context files appear under
    context_intro: str = (
        "Verschaff dir zuerst einen Überblick über die Kernmodule unter /work/proj/src/touchstone/ "
        "— lies sie. Die eigentliche Aufgabe kommt danach."
    )
    context_ack: str = (
        "Ich habe die Kernmodule gelesen und habe den Überblick. Was ist die Aufgabe?"
    )
    system_prompt: str
    max_tokens: int | None = None  # default per-item cap (None = server decides)
    sampling: ToolsSampling = Field(default_factory=ToolsSampling)
    tools: list[dict[str, Any]]
    items: list[ToolItem]

    @model_validator(mode="after")
    def _unique_ids(self) -> ToolsPack:
        ids = [i.id for i in self.items]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"duplicate item ids: {sorted(dup)}")
        return self

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        return {t["function"]["name"]: t["function"].get("parameters", {}) for t in self.tools}


def load_tools_pack(path: str | Path) -> ToolsPack:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"tools pack {path} did not parse to a mapping")
    pack = ToolsPack.model_validate(raw)
    needs = [it for it in pack.items if it.context]
    if needs:
        if not pack.context_dir:
            raise ValueError("items use `context` but the pack has no context_dir")
        base = Path(path).resolve().parent / pack.context_dir
        for it in needs:
            fx: dict[str, str] = {}
            for name in it.context:
                src = base / f"{name}.txt"
                if not src.is_file():
                    raise ValueError(f"item {it.id}: context file missing: {src}")
                fx[f"{pack.context_root}/{name}"] = src.read_text(encoding="utf-8")
            it.context_fixtures = fx
    return pack


# --------------------------------------------------------------------------- messages


def read_tool_output(path: str, content: str) -> str:
    """Render a file the way opencode's read tool returns it (1.18.x: ``N: line``)."""
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    body = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines))
    return (
        f"<path>{path}</path>\n<type>file</type>\n<content>\n{body}\n\n"
        f"(End of file - total {len(lines)} lines)\n</content>"
    )


def build_messages(pack: ToolsPack, item: ToolItem) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [{"role": "system", "content": pack.system_prompt}]
    if item.context_fixtures:
        msgs.append({"role": "user", "content": pack.context_intro})
        msgs += _read_exchange(item.context_fixtures, "call_ctx")
        msgs.append({"role": "assistant", "content": pack.context_ack})
    msgs.append({"role": "user", "content": item.prompt})
    if item.preread:
        msgs += _read_exchange({p: item.fixtures[p] for p in item.preread}, "call_read")
    return msgs


def _read_exchange(files: dict[str, str], prefix: str) -> list[dict[str, Any]]:
    """One assistant turn with parallel read calls + one tool result per file (opencode shape)."""
    calls = [
        {
            "id": f"{prefix}_{n}",
            "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"filePath": p})},
        }
        for n, p in enumerate(files)
    ]
    out: list[dict[str, Any]] = [{"role": "assistant", "content": "", "tool_calls": calls}]
    for n, (p, content) in enumerate(files.items()):
        out.append(
            {
                "role": "tool",
                "tool_call_id": f"{prefix}_{n}",
                "content": read_tool_output(p, content),
            }
        )
    return out


# --------------------------------------------------------------------------- stream → turn


@dataclass
class ToolStreamEvent:
    """One raw streaming fragment, engine-agnostic (produced by ``client.stream_tools``)."""

    content: str | None = None
    reasoning: str | None = None
    tc_index: int | None = None
    tc_id: str | None = None
    tc_name: str | None = None
    tc_args: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class ToolCall:
    index: int
    id: str
    name: str
    arguments: str  # raw string as streamed — may be "" (truncated) or invalid JSON


@dataclass
class ToolTurn:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_s: float | None = None  # first generated token of any kind
    t_first_tool_s: float | None = None
    e2e_s: float = 0.0
    error: str = ""


def collect_turn(events: Iterable[ToolStreamEvent], clock: Callable[[], float]) -> ToolTurn:
    """Fold streamed fragments into one turn. Tool-call fragments are keyed by ``index``
    (OpenAI streaming contract); name/id arrive once, arguments are concatenated."""
    t0 = clock()
    turn = ToolTurn()
    calls: dict[int, ToolCall] = {}
    content: list[str] = []
    reasoning: list[str] = []
    try:
        for ev in events:
            now = clock() - t0
            if (ev.content or ev.reasoning or ev.tc_index is not None) and turn.ttft_s is None:
                turn.ttft_s = now
            if ev.content:
                content.append(ev.content)
            if ev.reasoning:
                reasoning.append(ev.reasoning)
            if ev.tc_index is not None:
                if turn.t_first_tool_s is None:
                    turn.t_first_tool_s = now
                tc = calls.setdefault(ev.tc_index, ToolCall(ev.tc_index, "", "", ""))
                if ev.tc_id:
                    tc.id = ev.tc_id
                if ev.tc_name:
                    tc.name += ev.tc_name
                if ev.tc_args:
                    tc.arguments += ev.tc_args
            if ev.finish_reason:
                turn.finish_reason = ev.finish_reason
            if ev.prompt_tokens is not None:
                turn.prompt_tokens = ev.prompt_tokens
            if ev.completion_tokens is not None:
                turn.completion_tokens = ev.completion_tokens
    except Exception as e:  # connection drop / server error mid-stream: keep what arrived
        turn.error = f"{type(e).__name__}: {e}"
    turn.e2e_s = clock() - t0
    turn.content = "".join(content)
    turn.reasoning = "".join(reasoning)
    turn.tool_calls = [calls[i] for i in sorted(calls)]
    return turn


# --------------------------------------------------------------------------- schema check

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def validate_args(args: Any, schema: dict[str, Any]) -> list[str]:
    """Minimal JSON-schema check for flat tool parameters: object, required keys, types,
    enums, unknown keys. Returns a list of problems (empty = valid)."""
    if not isinstance(args, dict):
        return [f"arguments not an object ({type(args).__name__})"]
    problems: list[str] = []
    props: dict[str, Any] = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args:
            problems.append(f"missing required {req!r}")
    for k, v in args.items():
        if k not in props:
            problems.append(f"unknown key {k!r}")
            continue
        spec = props[k]
        t = spec.get("type")
        if isinstance(t, str) and t in _JSON_TYPES:
            ok = isinstance(v, _JSON_TYPES[t])
            if t in ("integer", "number") and isinstance(v, bool):
                ok = False  # bool is an int subclass in Python, not in JSON
            if t == "integer" and isinstance(v, float) and v.is_integer():
                ok = True  # 120000.0 is a JSON integer value
            if not ok:
                problems.append(f"{k!r}: expected {t}, got {type(v).__name__}")
        if "enum" in spec and v not in spec["enum"]:
            problems.append(f"{k!r}: {v!r} not in enum")
    return problems


def parse_args(call: ToolCall) -> tuple[Any, str]:
    if not call.arguments.strip():
        return None, "empty arguments"
    try:
        return json.loads(call.arguments), ""
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"


# --------------------------------------------------------------------------- code execution


def run_python(code: str, timeout_s: float = 20.0) -> tuple[bool, str]:
    """Execute model-written code + asserts in an isolated interpreter (``-I``, temp cwd)."""
    with tempfile.TemporaryDirectory(prefix="touchstone-tools-") as d:
        script = Path(d) / "check.py"
        script.write_text(code, encoding="utf-8")
        try:
            p = subprocess.run(
                [sys.executable, "-I", str(script)],
                cwd=d,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout_s:.0f}s"
    if p.returncode == 0:
        return True, ""
    tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
    return False, " | ".join(tail)[:400]


def js_syntax_ok(code: str) -> tuple[bool | None, str]:
    node = shutil.which("node")
    if node is None:
        return None, "node not found — not measured"
    with tempfile.TemporaryDirectory(prefix="touchstone-tools-") as d:
        f = Path(d) / "main.js"
        f.write_text(code, encoding="utf-8")
        p = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=20)
    if p.returncode == 0:
        return True, ""
    return False, (p.stderr.strip().splitlines() or ["syntax error"])[-1][:300]


def run_node(code: str, timeout_s: float = 20.0) -> tuple[bool | None, str]:
    node = shutil.which("node")
    if node is None:
        return None, "node not found — not measured"
    with tempfile.TemporaryDirectory(prefix="touchstone-tools-") as d:
        f = Path(d) / "check.js"
        f.write_text(code, encoding="utf-8")
        try:
            p = subprocess.run(
                [node, str(f)], cwd=d, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout_s:.0f}s"
    if p.returncode == 0:
        return True, ""
    tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
    return False, " | ".join(tail)[:400]


class _TagCounter(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.counts[tag] = self.counts.get(tag, 0) + 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.counts[tag] = self.counts.get(tag, 0) + 1


def html_tag_counts(doc: str) -> dict[str, int]:
    p = _TagCounter()
    p.feed(doc)
    p.close()
    return p.counts


# --------------------------------------------------------------------------- checks


@dataclass
class CheckResult:
    name: str
    ok: bool | None  # None = not measured (e.g. node missing) — never counted as pass
    detail: str = ""


def _calls_of(turn: ToolTurn, tool: str | None) -> list[ToolCall]:
    return [c for c in turn.tool_calls if tool is None or c.name == tool]


def _writes(turn: ToolTurn) -> dict[str, str]:
    """filePath → content of the LAST valid write call per path."""
    out: dict[str, str] = {}
    for c in _calls_of(turn, "write"):
        a, _ = parse_args(c)
        if isinstance(a, dict) and isinstance(a.get("filePath"), str):
            content = a.get("content")
            if isinstance(content, str):
                out[a["filePath"]] = content
    return out


def apply_edits(turn: ToolTurn, path: str, original: str) -> tuple[str | None, str]:
    """Apply every edit call on ``path`` in order, with opencode's semantics: oldString must
    occur; more than once is an error unless replaceAll. Returns (new_text | None, problem)."""
    text = original
    edits = []
    for c in _calls_of(turn, "edit"):
        a, err = parse_args(c)
        if isinstance(a, dict) and a.get("filePath") == path:
            edits.append(a)
        elif err and c.name == "edit":
            return None, f"edit call unparseable: {err}"
    if not edits:
        return None, "no edit call on this path"
    for n, a in enumerate(edits, 1):
        old, new = a.get("oldString"), a.get("newString")
        if not isinstance(old, str) or not isinstance(new, str):
            return None, f"edit {n}: oldString/newString missing or not strings"
        if old == new:
            return None, f"edit {n}: oldString == newString"
        if old == "":
            return None, f"edit {n}: empty oldString"
        k = text.count(old)
        if k == 0:
            return None, f"edit {n}: oldString not found"
        if k > 1 and a.get("replaceAll") is not True:
            return None, f"edit {n}: oldString ambiguous ({k} matches) without replaceAll"
        text = text.replace(old, new) if a.get("replaceAll") is True else text.replace(old, new, 1)
    return text, ""


def run_check(check: Check, turn: ToolTurn, item: ToolItem, schemas: dict[str, Any]) -> CheckResult:
    name = check.name()
    t = check.type
    if t == "calls":
        # Only COMPLETE calls count (arguments parse to an object) — a call truncated at the
        # budget edge arrives with a name but empty arguments and must not fill the quota.
        all_calls = _calls_of(turn, check.tool)
        n = sum(1 for c in all_calls if isinstance(parse_args(c)[0], dict))
        ok = (
            (check.count is None or n == check.count)
            and (check.min is None or n >= check.min)
            and (check.max is None or n <= check.max)
        )
        return CheckResult(name, ok, f"{n} complete of {len(all_calls)} call(s)")
    if t == "no_tools":
        ok = not turn.tool_calls and bool(turn.content.strip())
        return CheckResult(
            name, ok, f"{len(turn.tool_calls)} call(s), content={bool(turn.content)}"
        )
    if t == "schema_valid":
        if not turn.tool_calls:
            return CheckResult(name, False, "no tool calls")
        problems = []
        for c in turn.tool_calls:
            if c.name not in schemas:
                problems.append(f"#{c.index} unknown tool {c.name!r}")
                continue
            a, err = parse_args(c)
            if err:
                problems.append(f"#{c.index} {c.name}: {err[:80]}")
                continue
            problems += [f"#{c.index} {c.name}: {p}" for p in validate_args(a, schemas[c.name])]
        return CheckResult(name, not problems, "; ".join(problems)[:400])
    if t == "no_empty_args":
        empty = [c.index for c in turn.tool_calls if not c.arguments.strip()]
        return CheckResult(name, not empty, f"empty: {empty}" if empty else "")
    if t == "finish_reason":
        return CheckResult(name, turn.finish_reason == check.value, str(turn.finish_reason))
    if t == "call_paths":
        paths = []
        for c in _calls_of(turn, check.tool):
            a, _ = parse_args(c)
            if isinstance(a, dict):
                paths.append(a.get("filePath"))
        want = sorted(check.value or [])
        return CheckResult(
            name, sorted(p for p in paths if p) == want and len(paths) == len(want), f"got {paths}"
        )
    if t in ("arg_equals", "arg_regex"):
        cs = _calls_of(turn, check.tool)
        if not cs:
            return CheckResult(name, False, f"no {check.tool} call")
        a, err = parse_args(cs[0])
        if not isinstance(a, dict):
            return CheckResult(name, False, err or "arguments not an object")
        if check.key not in a:
            return CheckResult(name, False, f"key {check.key!r} missing")
        v = a[check.key]
        if t == "arg_equals":
            same = v == check.value and type(v) is type(check.value)
            if isinstance(check.value, int) and isinstance(v, float) and v.is_integer():
                same = int(v) == check.value
            return CheckResult(name, same, f"got {v!r}")
        ok = bool(re.search(check.pattern or "", str(v), re.S))
        return CheckResult(name, ok != check.absent, f"got {str(v)[:120]!r}")
    if t.startswith("write_"):
        writes = _writes(turn)
        path = check.path or ""
        if path not in writes:
            return CheckResult(name, False, f"no valid write to {path} (have: {sorted(writes)})")
        body = writes[path]
        if t == "write_regex":
            ok = bool(re.search(check.pattern or "", body, re.S | re.M))
            return CheckResult(name, ok != check.absent, "")
        if t == "write_json_equals":
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as e:
                return CheckResult(name, False, f"file is not JSON: {e}")
            return CheckResult(
                name, parsed == check.value, "" if parsed == check.value else "differs"
            )
        if t == "write_python":
            ok, det = run_python(body + "\n\n" + (check.test or ""))
            return CheckResult(name, ok, det)
        if t == "write_html":
            counts = html_tag_counts(body)
            short = {
                k: (counts.get(k, 0), v) for k, v in check.tags.items() if counts.get(k, 0) < v
            }
            return CheckResult(name, not short, f"too few (have, need): {short}" if short else "")
        if t == "write_js_syntax":
            ok_js, det = js_syntax_ok(body)
            return CheckResult(name, ok_js, det)
        if t == "write_check":
            ok, det = run_python(f"CONTENT = {body!r}\n\n" + (check.test or ""))
            return CheckResult(name, ok, det)
        if t == "write_node":
            ok_n, det = run_node(body + "\n\n" + (check.test or ""))
            return CheckResult(name, ok_n, det)
    if t in ("edit_applies", "edited_python", "edited_regex"):
        path = check.path or ""
        new, problem = apply_edits(turn, path, item.fixtures[path])
        if new is None:
            return CheckResult(name, False, problem)
        if t == "edit_applies":
            return CheckResult(name, True, "")
        if t == "edited_python":
            ok, det = run_python(new + "\n\n" + (check.test or ""))
            return CheckResult(name, ok, det)
        ok = bool(re.search(check.pattern or "", new, re.S | re.M))
        return CheckResult(name, ok != check.absent, "")
    raise ValueError(f"unhandled check type {t}")  # pragma: no cover — Literal guards this


def run_checks(item: ToolItem, turn: ToolTurn, schemas: dict[str, Any]) -> list[CheckResult]:
    return [run_check(c, turn, item, schemas) for c in item.checks]


# --------------------------------------------------------------------------- result row


@dataclass
class ToolResponse:
    pack_id: str
    pack_version: int
    model: str
    quant: str
    item_id: str
    category: str
    repeat: int
    passed: bool  # every measured check ok and at least one measured
    checks_ok: int
    checks_measured: int
    checks: list[dict[str, Any]]
    finish_reason: str | None
    truncated: bool  # finish_reason == "length"
    n_calls: int
    n_empty_args: int
    tool_calls: list[dict[str, Any]]
    content: str
    reasoning_chars: int
    prompt_tokens: int | None
    completion_tokens: int | None
    ttft_s: float | None
    t_first_tool_s: float | None
    e2e_s: float
    error: str
    t_start: float
    reasoning_text: str = ""  # persisted only when there is no tool call and no content


def make_response(
    pack: ToolsPack,
    item: ToolItem,
    model: str,
    quant: str,
    repeat: int,
    turn: ToolTurn,
    results: list[CheckResult],
    t_start: float,
) -> ToolResponse:
    measured = [r for r in results if r.ok is not None]
    ok = sum(1 for r in measured if r.ok)
    bare = not turn.tool_calls and not turn.content.strip()
    return ToolResponse(
        pack_id=pack.id,
        pack_version=pack.version,
        model=model,
        quant=quant,
        item_id=item.id,
        category=item.category,
        repeat=repeat,
        passed=bool(measured) and ok == len(measured) and not turn.error,
        checks_ok=ok,
        checks_measured=len(measured),
        checks=[asdict(r) for r in results],
        finish_reason=turn.finish_reason,
        truncated=turn.finish_reason == "length",
        n_calls=len(turn.tool_calls),
        n_empty_args=sum(1 for c in turn.tool_calls if not c.arguments.strip()),
        tool_calls=[asdict(c) for c in turn.tool_calls],
        content=turn.content,
        reasoning_chars=len(turn.reasoning),
        prompt_tokens=turn.prompt_tokens,
        completion_tokens=turn.completion_tokens,
        ttft_s=turn.ttft_s,
        t_first_tool_s=turn.t_first_tool_s,
        e2e_s=turn.e2e_s,
        error=turn.error,
        t_start=t_start,
        reasoning_text=turn.reasoning if bare else "",
    )


def load_tool_responses(path: str | Path) -> list[ToolResponse]:
    """Tolerates a half-written final line (interrupted run)."""
    out: list[ToolResponse] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(ToolResponse(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


# --------------------------------------------------------------------------- orchestration

StreamFn = Callable[..., Iterator[ToolStreamEvent]]


def run_tools(
    pack: ToolsPack,
    models: list[tuple[str, str, dict[str, Any]]],  # (id, quant, extra_body)
    stream_fn: StreamFn,
    out_dir: Path,
    *,
    resume: bool = False,
    clock: Callable[[], float] = time.perf_counter,
    wall: Callable[[], float] = time.time,
    log: Callable[[str], None] = print,
) -> list[ToolResponse]:
    """Drive model × item × repeat; append each response as it finishes; resume skips done
    cells (a transport error is recorded, not retried — delete its line to redo it)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "responses.jsonl"
    prior = load_tool_responses(path) if resume else []
    if not resume and path.exists():
        path.unlink()
    done = {(r.model, r.item_id, r.repeat) for r in prior}
    schemas = pack.tool_schemas()
    results = list(prior)
    total = sum(it.repeats for it in pack.items) * len(models)
    i = len(done)
    for model, quant, extra_body in models:
        for item in pack.items:
            for rep in range(item.repeats):
                if (model, item.id, rep) in done:
                    continue
                i += 1
                t_start = wall()
                cap = item.max_tokens if item.max_tokens is not None else pack.max_tokens
                try:
                    events = stream_fn(
                        model=model,
                        messages=build_messages(pack, item),
                        tools=pack.tools,
                        max_tokens=cap,
                        temperature=pack.sampling.temperature,
                        seed=pack.sampling.seed,
                        extra_body=extra_body or None,
                    )
                    turn = collect_turn(events, clock)
                except Exception as e:  # request rejected before streaming began
                    turn = ToolTurn(error=f"{type(e).__name__}: {e}")
                res = run_checks(item, turn, schemas)
                resp = make_response(pack, item, model, quant, rep, turn, res, t_start)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(resp), ensure_ascii=False) + "\n")
                results.append(resp)
                mark = "✓" if resp.passed else ("✗ ERR" if resp.error else "✗")
                log(
                    f"[{i}/{total}] {model} {item.id}#{rep} {mark} "
                    f"{resp.checks_ok}/{resp.checks_measured} calls={resp.n_calls} "
                    f"finish={resp.finish_reason} tok={resp.completion_tokens} "
                    f"{resp.e2e_s:.0f}s"
                )
    return results


# --------------------------------------------------------------------------- report / compare

CSV_COLUMNS = [
    "model", "quant", "item_id", "category", "repeat", "passed", "checks_ok", "checks_measured",
    "finish_reason", "truncated", "n_calls", "n_empty_args", "reasoning_chars", "prompt_tokens",
    "completion_tokens", "ttft_s", "t_first_tool_s", "e2e_s", "error", "failed_checks",
]  # fmt: skip


def write_results_csv(rows: list[ToolResponse], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            failed = "; ".join(c["name"] for c in r.checks if c["ok"] is False)
            d = asdict(r)
            d["failed_checks"] = failed
            w.writerow([d[c] for c in CSV_COLUMNS])


def _summary(rows: list[ToolResponse]) -> dict[str, Any]:
    n = len(rows)
    return {
        "items": n,
        "passed": sum(r.passed for r in rows),
        "checks_ok": sum(r.checks_ok for r in rows),
        "checks_measured": sum(r.checks_measured for r in rows),
        "not_measured": sum(1 for r in rows for c in r.checks if c["ok"] is None),
        "truncated": sum(r.truncated for r in rows),
        "empty_args_calls": sum(r.n_empty_args for r in rows),
        "calls": sum(r.n_calls for r in rows),
        "errors": sum(1 for r in rows if r.error),
        "completion_tokens": sum(r.completion_tokens or 0 for r in rows),
        "e2e_s": sum(r.e2e_s for r in rows),
    }


def render_report_md(pack: ToolsPack, rows: list[ToolResponse], meta: dict[str, Any]) -> str:
    lines = [f"# Tools-Pack `{pack.id}` v{pack.version} — {pack.title}", ""]
    for k, v in meta.items():
        lines.append(f"- **{k}:** {v}")
    lines.append("")
    by_model: dict[str, list[ToolResponse]] = {}
    for r in rows:
        by_model.setdefault(r.model, []).append(r)
    lines += [
        "| Modell | Quant | Items bestanden | Checks ok | nicht gemessen | length-Abbrüche "
        "| leere arguments | Fehler | Tokens | Laufzeit |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m, rs in by_model.items():
        s = _summary(rs)
        lines.append(
            f"| {m} | {rs[0].quant} | {s['passed']}/{s['items']} | "
            f"{s['checks_ok']}/{s['checks_measured']} | {s['not_measured']} | {s['truncated']} | "
            f"{s['empty_args_calls']}/{s['calls']} | {s['errors']} | {s['completion_tokens']} | "
            f"{s['e2e_s'] / 60:.0f} min |"
        )
    lines += ["", "## Je Item", "", "| Modell | Item | Kat. | ✓ | Checks | finish | Calls | "
              "fehlgeschlagen |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in rows:
        failed = "; ".join(
            f"{c['name']} ({c['detail'][:60]})" for c in r.checks if c["ok"] is False
        )
        lines.append(
            f"| {r.model} | {r.item_id}#{r.repeat} | {r.category} | {'✓' if r.passed else '✗'} | "
            f"{r.checks_ok}/{r.checks_measured} | {r.finish_reason} | {r.n_calls} | "
            f"{failed.replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar = binomial sign test on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    tail: float = sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


@dataclass
class PairedComparison:
    label_a: str
    label_b: str
    pairs: int
    both_pass: int
    only_a: int
    only_b: int
    neither: int
    p_mcnemar: float
    checks_a: tuple[int, int]
    checks_b: tuple[int, int]
    per_item: list[tuple[str, bool, bool]]


def compare_bundles(a: list[ToolResponse], b: list[ToolResponse]) -> PairedComparison:
    """Pair by (item_id, repeat). Item pass is binary → discordant pairs + exact McNemar."""
    ka = {(r.item_id, r.repeat): r for r in a}
    kb = {(r.item_id, r.repeat): r for r in b}
    keys = sorted(set(ka) & set(kb))
    per = [(f"{k[0]}#{k[1]}", ka[k].passed, kb[k].passed) for k in keys]
    only_a = sum(1 for _, x, y in per if x and not y)
    only_b = sum(1 for _, x, y in per if y and not x)
    ca = (sum(ka[k].checks_ok for k in keys), sum(ka[k].checks_measured for k in keys))
    cb = (sum(kb[k].checks_ok for k in keys), sum(kb[k].checks_measured for k in keys))
    return PairedComparison(
        label_a=a[0].quant or a[0].model if a else "A",
        label_b=b[0].quant or b[0].model if b else "B",
        pairs=len(keys),
        both_pass=sum(1 for _, x, y in per if x and y),
        only_a=only_a,
        only_b=only_b,
        neither=sum(1 for _, x, y in per if not x and not y),
        p_mcnemar=mcnemar_exact_p(only_a, only_b),
        checks_a=ca,
        checks_b=cb,
        per_item=per,
    )


def render_compare_md(cmp: PairedComparison) -> str:
    a, b = cmp.label_a, cmp.label_b
    lines = [
        f"# Paarvergleich Tools-Pack: {a} vs {b}",
        "",
        f"- Paare (Item × Wiederholung): {cmp.pairs}",
        f"- beide bestanden: {cmp.both_pass} · nur {a}: {cmp.only_a} · nur {b}: {cmp.only_b} · "
        f"keiner: {cmp.neither}",
        f"- exakter McNemar-Test (zweiseitig) auf die {cmp.only_a + cmp.only_b} diskordanten "
        f"Paare: p = {cmp.p_mcnemar:.3f}",
        f"- Einzel-Checks: {a} {cmp.checks_a[0]}/{cmp.checks_a[1]} · "
        f"{b} {cmp.checks_b[0]}/{cmp.checks_b[1]}",
        "",
        f"| Item | {a} | {b} |",
        "|---|---|---|",
    ]
    for k, x, y in cmp.per_item:
        lines.append(f"| {k} | {'✓' if x else '✗'} | {'✓' if y else '✗'} |")
    return "\n".join(lines) + "\n"
