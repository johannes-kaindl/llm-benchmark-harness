# tests/test_gui_export_yaml.py
import pytest

pytest.importorskip("fastapi")
import yaml
from fastapi.testclient import TestClient

from touchstone.gui import app as gui_app
from touchstone.gui.control import RunRegistry


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


def test_export_yaml_config_roundtrips(tmp_path):
    r = _client(tmp_path).get("/export-yaml?kind=config&path=config.m5.yaml")
    assert r.status_code == 200
    assert "yaml" in r.headers["content-type"]
    parsed = yaml.safe_load(r.text)
    assert isinstance(parsed, dict)
    assert parsed["machine"] == "M5-32GB"
    assert "config.m5.yaml" in r.headers.get("content-disposition", "")


def test_export_yaml_pack_roundtrips(tmp_path):
    r = _client(tmp_path).get("/export-yaml?kind=pack&path=packs/ndassist.yaml")
    assert r.status_code == 200
    assert "yaml" in r.headers["content-type"]
    parsed = yaml.safe_load(r.text)
    assert isinstance(parsed, dict)
    assert parsed["id"]


def test_export_yaml_rejects_bad_kind(tmp_path):
    r = _client(tmp_path).get("/export-yaml?kind=secret&path=config.m5.yaml")
    assert r.status_code == 404


def test_export_yaml_rejects_unoffered_path(tmp_path):
    r = _client(tmp_path).get("/export-yaml?kind=config&path=../etc/passwd")
    assert r.status_code == 404


def test_export_yaml_rejects_pack_as_config(tmp_path):
    # A pack path offered under the pack glob must not be readable via kind=config.
    r = _client(tmp_path).get("/export-yaml?kind=config&path=packs/ndassist.yaml")
    assert r.status_code == 404


def test_export_yaml_config_redacts_api_key(tmp_path):
    # SECURITY: the downloadable config must never carry the endpoint api_key in cleartext
    # (the on-screen viewer masks it; the download must not be a side-channel).
    r = _client(tmp_path).get("/export-yaml?kind=config&path=config.m5.yaml")
    assert r.status_code == 200
    parsed = yaml.safe_load(r.text)
    assert parsed["endpoint"]["api_key"] == "<redacted>"
