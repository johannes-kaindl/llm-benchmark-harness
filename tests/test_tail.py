from ramcheck.tail import read_new


def test_missing_file_yields_nothing(tmp_path):
    lines, off = read_new(tmp_path / "nope.jsonl", 0)
    assert lines == [] and off == 0


def test_reads_complete_lines_and_advances_offset(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("a\nb\n", encoding="utf-8")
    lines, off = read_new(p, 0)
    assert lines == ["a", "b"] and off == 4


def test_partial_last_line_is_not_consumed_until_complete(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("a\nb", encoding="utf-8")  # 'b' has no newline yet
    lines, off = read_new(p, 0)
    assert lines == ["a"] and off == 2  # only 'a\n' consumed
    with p.open("a", encoding="utf-8") as fh:
        fh.write("\n")  # complete the partial 'b' line
    lines2, off2 = read_new(p, off)
    assert lines2 == ["b"] and off2 == 4


def test_second_call_returns_only_new(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text("a\n", encoding="utf-8")
    _, off = read_new(p, 0)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("c\n")
    lines, _ = read_new(p, off)
    assert lines == ["c"]
