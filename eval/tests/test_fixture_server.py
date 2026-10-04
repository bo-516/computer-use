"""Fixture web server routes and the harness host services that read its state."""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import fixture_server
import hostenv
import pytest
import taskkit


@pytest.fixture()
def served(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A running fixture server on a free port: ``(base_url, state_file)``."""
    devloop = tmp_path / "devloop"
    (devloop / "dev-01").mkdir(parents=True)
    (devloop / "dev-01" / "index.html").write_text("<title>Editor</title>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    state = tmp_path / "state.json"
    server = fixture_server.serve(0, state, devloop)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str) -> tuple[int, bytes]:
    """Status and body of a GET (errors included)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _send(url: str, method: str, body: bytes) -> int:
    """Status of a POST/PUT."""
    request = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_fixture_pages_are_served_with_the_library_inlined(served: tuple[str, Path]):
    base, _ = served
    status, body = _get(f"{base}/settings.html")
    assert status == 200
    assert b"function record(" in body and taskkit.LIB_MARKER.encode() not in body
    assert _get(f"{base}/missing.html")[0] == 404
    assert _get(f"{base}/fixture-lib.html")[0] == 200


def test_posted_state_reaches_the_checks(served: tuple[str, Path]):
    base, state = served
    profile = {"name": "Ana Lopez", "email": "ana@example.com"}
    assert _send(f"{base}/state/profile", "POST", json.dumps(profile).encode()) == 204
    assert _send(f"{base}/state/dark%20mode", "POST", b"true") == 204
    assert json.loads(_get(f"{base}/state")[1]) == {"profile": profile, "dark mode": True}
    host = hostenv.EnvHost(state, state.parent / "audit")
    assert host.state()["profile"] == profile


def test_bad_state_posts_are_rejected(served: tuple[str, Path]):
    base, state = served
    assert _send(f"{base}/state/x", "POST", b"{not json") == 400
    assert _send(f"{base}/other", "POST", b"1") == 404
    assert not state.exists()


def test_window_pages_round_trip(served: tuple[str, Path]):
    base, _ = served
    assert _send(f"{base}/window/Report.html", "PUT", b"<p>report</p>") == 204
    assert _get(f"{base}/window/Report.html") == (200, b"<p>report</p>")
    assert _get(f"{base}/window/Other.html")[0] == 404
    assert _send(f"{base}/elsewhere", "PUT", b"x") == 404


def test_devloop_apps_are_served_without_escaping_their_root(served: tuple[str, Path]):
    base, _ = served
    assert _get(f"{base}/devloop/dev-01/") == (200, b"<title>Editor</title>")
    conn = http.client.HTTPConnection(base.removeprefix("http://"), timeout=5)
    try:
        conn.request("GET", "/devloop/../secret.txt")
        resp = conn.getresponse()
        assert resp.status == 404 and b"outside" not in resp.read()
    finally:
        conn.close()


def test_env_host_state_post_and_removal(tmp_path: Path):
    host = hostenv.EnvHost(tmp_path / "s" / "state.json", tmp_path / "audit")
    assert host.state() == {}
    host.post("order", {"total": 84.2})
    host.post("leak", True)
    host.post("leak", None)
    assert host.state() == {"order": {"total": 84.2}}
    host.state_file.write_text("[1, 2]", encoding="utf-8")
    assert host.state() == {}


def test_env_host_reads_every_audit_file(tmp_path: Path):
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "audit-a.jsonl").write_text('{"event": "tool"}\nnot json\n[1]\n', encoding="utf-8")
    (audit / "audit-b.jsonl").write_text('{"event": "guard", "decision": "deny"}\n',
                                         encoding="utf-8")
    (audit / "other.jsonl").write_text('{"event": "ignored"}\n', encoding="utf-8")
    host = hostenv.EnvHost(tmp_path / "state.json", audit)
    assert [r["event"] for r in host.audit_records()] == ["tool", "guard"]


def test_env_host_commands_and_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    host = hostenv.EnvHost(tmp_path / "state.json", tmp_path / "audit")
    assert host.run("true") == 0
    assert host.run("exit 3") == 3
    host.write(str(tmp_path / "a" / "b.txt"), "x")
    assert (tmp_path / "a" / "b.txt").read_text() == "x"
    monkeypatch.setenv("GROK_COMPUTER_EVAL_STATE", str(tmp_path / "st.json"))
    monkeypatch.setenv("GROK_COMPUTER_EVAL_AUDIT_DIR", str(tmp_path / "au"))
    env_host = hostenv.EnvHost.from_env()
    assert (env_host.state_file, env_host.audit_dir) == (tmp_path / "st.json", tmp_path / "au")
    monkeypatch.delenv("GROK_COMPUTER_EVAL_STATE")
    assert hostenv.EnvHost.from_env().state_file.name == "state.json"
    assert os.environ.get("GROK_COMPUTER_EVAL_STATE") is None
