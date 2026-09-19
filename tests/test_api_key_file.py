"""api_key_file: the endpoint key is read from a file so it never lands in a (tracked) YAML."""

import pytest
from pydantic import ValidationError

from touchstone.config import Config, Endpoint, apply_overrides
from touchstone.judge import JudgeEndpoint


def test_key_read_from_file_and_stripped(tmp_path):
    kf = tmp_path / "tok"
    kf.write_text("sk-secret\n")
    ep = Endpoint(base_url="https://x/v1", api_key_file=str(kf))
    assert ep.api_key == "sk-secret"


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tok").write_text("abc")
    assert Endpoint(base_url="https://x/v1", api_key_file="~/.tok").api_key == "abc"


def test_missing_file_is_a_validation_error_naming_the_path(tmp_path):
    with pytest.raises(ValidationError, match="nope"):
        Endpoint(base_url="https://x/v1", api_key_file=str(tmp_path / "nope"))


def test_empty_file_is_rejected(tmp_path):
    kf = tmp_path / "tok"
    kf.write_text("  \n")
    with pytest.raises(ValidationError, match="leer"):
        Endpoint(base_url="https://x/v1", api_key_file=str(kf))


def test_without_file_the_inline_key_stays():
    assert Endpoint(base_url="https://x/v1").api_key == "not-needed"
    assert Endpoint(base_url="https://x/v1", api_key="k").api_key == "k"


def test_judge_endpoint_supports_it_too(tmp_path):
    kf = tmp_path / "tok"
    kf.write_text("judge-key")
    assert JudgeEndpoint(base_url="https://x/v1", api_key_file=str(kf)).api_key == "judge-key"


def test_survives_the_override_revalidation(tmp_path):
    # apply_overrides round-trips through model_dump → model_validate; the resolved key
    # plus the file path must re-validate cleanly (no "both set" conflict).
    kf = tmp_path / "tok"
    kf.write_text("sk-secret")
    cfg = Config.model_validate(
        {
            "endpoint": {"base_url": "https://x/v1", "api_key_file": str(kf)},
            "models": [{"id": "a"}],
        }
    )
    out = apply_overrides(cfg, {"seed": 7})
    assert out.endpoint.api_key == "sk-secret"
    assert out.seed == 7
