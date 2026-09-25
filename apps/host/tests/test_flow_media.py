"""Registered Flow media stays authenticated, read-only, and type-limited."""

from dataclasses import replace

from personal_agent_host.app import BearerGuard
from personal_agent_host.config import FlowPolicy
from personal_agent_host.flow_media import FlowMediaHTTP, media_target
from starlette.applications import Starlette
from starlette.testclient import TestClient

from test_egress import _config


def test_media_target_accepts_only_registered_flow_media(tmp_path):
    config = replace(_config(tmp_path), flows=(FlowPolicy("workspace", "workspace", "http://127.0.0.1:4176"),))
    assert media_target(config, "workspace", "examples", "metal.png", None) == "http://127.0.0.1:4176/examples/metal.png"
    assert media_target(config, "workspace", "examples", "recording.m4a", None).endswith("/examples/recording.m4a")
    assert media_target(config, "workspace", "files", "content", "photos/part A.png").endswith("workspaceId=workspace&path=photos%2Fpart+A.png")
    assert media_target(config, "workspace", "files", "preview", "docs/report.pdf").endswith("/api/flow/files/preview?workspaceId=workspace&path=docs%2Freport.pdf")
    assert media_target(config, "workspace", "files", "preview", "docs/report.pdf", "2").endswith("/api/flow/files/preview?workspaceId=workspace&path=docs%2Freport.pdf&page=2")
    for bad_page in ("0", "1-2", "10001"):
        try:
            media_target(config, "workspace", "files", "preview", "docs/report.pdf", bad_page)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe PDF page accepted: {bad_page}")
    for args in (
        ("missing", "examples", "metal.png", None),
        ("workspace", "examples", "../secret.png", None),
        ("workspace", "examples", "script.svg", None),
        ("workspace", "assets", "not-a-hash.png", None),
        ("workspace", "files", "content", None),
    ):
        try:
            media_target(config, *args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe media path accepted: {args}")


def test_media_http_requires_bearer_and_forwards_only_safe_range(tmp_path, monkeypatch):
    from personal_agent_host import flow_media

    assert "application/pdf" in flow_media._ALLOWED_TYPES
    assert "audio/mp4" in flow_media._ALLOWED_TYPES
    assert "audio/aac" in flow_media._ALLOWED_TYPES
    config = replace(_config(tmp_path), flows=(FlowPolicy("workspace", "workspace", "http://127.0.0.1:4176"),))
    calls = []

    def read(url, method, byte_range):
        calls.append((url, method, byte_range))
        return 206, {"Content-Type": "image/png", "Content-Range": "bytes 0-2/10"}, b"png"

    monkeypatch.setattr(flow_media, "_read_media", read)
    client = TestClient(BearerGuard(Starlette(routes=FlowMediaHTTP(config).routes()), "secret"))
    route = "/flow-media/workspace/examples/metal.png"
    assert client.get(route).status_code == 401
    assert client.post(route, headers={"Authorization": "Bearer secret"}).status_code == 405
    assert client.get("/flow-media/workspace/examples/script.svg", headers={"Authorization": "Bearer secret"}).status_code == 404
    response = client.get(route, headers={"Authorization": "Bearer secret", "Range": "bytes=0-2"})
    assert response.status_code == 206 and response.content == b"png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-range"] == "bytes 0-2/10"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert calls == [("http://127.0.0.1:4176/examples/metal.png", "GET", "bytes=0-2")]


def test_flow_text_preview_proxy_accepts_only_matching_file_json(monkeypatch):
    import json
    from personal_agent_host import flow_media

    class Reply:
        status = 200

        def __init__(self, body, content_type="application/json; charset=utf-8"):
            self.body = body
            self.headers = {"Content-Type": content_type}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, limit):
            return self.body[:limit]

    class Opener:
        reply = None

        def open(self, *_args, **_kwargs):
            return self.reply

    opener = Opener()
    monkeypatch.setattr(flow_media, "_OPENER", opener)
    url = "http://127.0.0.1:4176/api/flow/files/content?workspaceId=workspace&path=notes%2Fmemo.md"
    payload = {"type": "text/plain", "path": "notes/memo.md", "content": "첫 번째 메모\n두 번째 메모"}
    opener.reply = Reply(json.dumps(payload).encode())
    status, headers, body = flow_media._read_media(url, "GET", None)
    assert status == 200 and headers["Content-Type"] == "application/json" and json.loads(body) == payload
    assert flow_media._read_media(url, "HEAD", None)[0] == 200

    opener.reply = Reply(json.dumps({**payload, "path": "notes/other.md"}).encode())
    assert flow_media._read_media(url, "GET", None)[0] == 415
    opener.reply = Reply(json.dumps(payload).encode())
    assert flow_media._read_media("http://127.0.0.1:4176/examples/data.json", "GET", None)[0] == 415
    assert flow_media._read_media(url, "GET", "bytes=0-2")[0] == 415
    opener.reply = Reply(b"x" * (flow_media.MAX_TEXT_JSON + 1))
    assert flow_media._read_media(url, "GET", None)[0] == 413
