"""Binary HTTP limits, authentication, ranges and passive HTML preview."""

from __future__ import annotations

from personal_agent_host.app import BearerGuard
from personal_agent_host.transfer_http import TransferHTTP, passive_html
from personal_agent_host.transfers import CHUNK_BYTES, Transfers
from starlette.applications import Starlette
from starlette.testclient import TestClient
from test_egress import _config

UPSTREAM = "unit-upstream-not-a-real-credential"


def test_http_auth_ranges_and_safe_preview(tmp_path):
    config = _config(tmp_path)
    root = config.root("workspace")
    (root.root / "한글.txt").write_text("hello world")
    transfers = Transfers(config)
    receipt = transfers.begin_download(root, "한글.txt")
    app = BearerGuard(Starlette(routes=TransferHTTP(transfers).routes()), UPSTREAM)
    client = TestClient(app)
    path = f"/transfers/{receipt['transfer_id']}/content"
    headers = {
        "Authorization": f"Bearer {UPSTREAM}",
        "X-Toolkit-Transfer-Token": receipt["token"],
    }
    assert client.get(path).status_code == 401
    assert (
        client.get(path, headers={"Authorization": f"Bearer {UPSTREAM}"}).status_code
        == 404
    )
    result = client.get(path, headers={**headers, "Range": "bytes=1-4"})
    assert result.status_code == 206 and result.content == b"ello"
    assert result.headers["Content-Range"] == "bytes 1-4/11"
    assert "no-store" in result.headers["Cache-Control"]
    assert (
        client.get(path, headers={**headers, "Range": "bytes=999-"}).status_code == 416
    )
    assert client.get(path, headers=headers).content == b"hello world"


def test_upload_chunk_http_limit_and_commit(tmp_path):
    config = _config(tmp_path)
    transfers = Transfers(config)
    root = config.root("workspace")
    receipt = transfers.begin_upload(root, "binary.bin", 3, "absent")
    client = TestClient(
        BearerGuard(Starlette(routes=TransferHTTP(transfers).routes()), UPSTREAM)
    )
    path = f"/transfers/{receipt['transfer_id']}"
    headers = {
        "Authorization": f"Bearer {UPSTREAM}",
        "X-Toolkit-Transfer-Token": receipt["token"],
        "Content-Type": "application/octet-stream",
        "Upload-Offset": "0",
    }
    assert (
        client.put(
            path + "/chunk", content=b"x" * (CHUNK_BYTES + 1), headers=headers
        ).status_code
        == 413
    )
    assert not (root.root / "binary.bin").exists()
    assert (
        client.put(path + "/chunk", content=b"abc", headers=headers).status_code == 200
    )
    assert client.post(path + "/commit", headers=headers).status_code == 200
    assert (root.root / "binary.bin").read_bytes() == b"abc"


def test_html_cannot_navigate_run_code_or_load_external_assets():
    result = passive_html(
        '<meta http-equiv="refresh" content="0;url=https://outside.test">'
        '<base href="https://outside.test"><script>alert(1)</script>'
        '<iframe src="https://outside.test"></iframe><form action="https://outside.test">'
        '<a href="https://outside.test" onclick="alert(1)">safe link label</a>'
        '<img src="https://outside.test/pixel"><div style="color:red">document</div></form>'
    )
    for forbidden in [
        "http-equiv",
        "<script",
        "<iframe",
        "<form",
        "onclick",
        "href=",
        "outside.test",
    ]:
        assert forbidden not in result
    assert "safe link label" in result and "document" in result
