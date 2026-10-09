"""GET/HEAD /upload/<name>: read a sent photo/video/file back for chat previews. Runs the REAL app
(scope middleware + aiohttp FileResponse) against a scratch data dir, so Range/HEAD/ETag are the
server's real behaviour, not a model of it. Same contract as the fleet gateway (flag upload_fetch).
"""
import asyncio
import io
import shutil
import subprocess

import pytest
from aiohttp.test_utils import TestClient, TestServer

import scripts.watch_gateway as G

READER = {"id": "dev-read", "label": "quest", "scopes": ["read"]}
MESSAGE_ONLY = {"id": "dev-msg", "label": "x", "scopes": ["message"]}
IMG = "1791529000_abc123.png"
VID = "1791529001_vid001.mp4"
HTML = "1791529002_doc001.html"
SVGISH = "1791529003_doc002.txt"


def _png(w=1200, h=600):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def orch(tmp_path, monkeypatch):
    up = tmp_path / "state" / "uploads"
    up.mkdir(parents=True)
    (up / IMG).write_bytes(_png())
    (up / HTML).write_text("<script>alert(1)</script>")
    (up / SVGISH).write_text("hello")
    (tmp_path / "state" / "secret.txt").write_text("TOP SECRET")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))

    def principal(request):
        tok = request.headers.get("Authorization", "")
        return {"Bearer read": READER, "Bearer msg": MESSAGE_ONLY}.get(tok)
    monkeypatch.setattr(G, "resolve_principal", principal)
    return tmp_path


def _call(method, path, token="read", headers=None):
    async def go():
        async with TestClient(TestServer(G.build_app())) as c:
            h = dict(headers or {})
            if token:
                h["Authorization"] = f"Bearer {token}"
            r = await c.request(method, path, headers=h)
            return r.status, r.headers.copy(), await r.read()   # CIMultiDict: aiohttp sends "Etag"
    return asyncio.run(go())


# ---- auth ---------------------------------------------------------------------------------------

def test_no_token_is_401(orch):
    assert _call("GET", f"/upload/{IMG}", token=None)[0] == 401


def test_a_device_without_read_is_403(orch):
    assert _call("GET", f"/upload/{IMG}", token="msg")[0] == 403


def test_head_is_scoped_too(orch):
    assert _call("HEAD", f"/upload/{IMG}", token=None)[0] == 401


# ---- (a) type + length, HEAD --------------------------------------------------------------------

def test_image_served_inline_with_type_and_length(orch):
    st, h, body = _call("GET", f"/upload/{IMG}")
    assert st == 200 and body == (orch / "state" / "uploads" / IMG).read_bytes()
    assert h["Content-Type"] == "image/png"
    assert int(h["Content-Length"]) == len(body)
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in h["Content-Security-Policy"]


def test_head_gives_type_and_length_without_bytes(orch):
    st, h, body = _call("HEAD", f"/upload/{IMG}")
    assert st == 200 and body == b""
    assert h["Content-Type"] == "image/png"
    assert int(h["Content-Length"]) == (orch / "state" / "uploads" / IMG).stat().st_size


# ---- never executable on the gateway's (Funnel-public) origin -----------------------------------

def test_html_is_an_attachment_never_rendered(orch):
    st, h, body = _call("GET", f"/upload/{HTML}")
    assert st == 200
    assert h["Content-Type"] == "application/octet-stream"
    assert h["Content-Disposition"].startswith("attachment")
    assert HTML in h["Content-Disposition"]


# ---- (b) name hardening -------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["..%2Fsecret.txt", "%2E%2E%2Fsecret.txt", "secret.txt", "a%00b.png",
                                  "..", "1791529000_abc123.png%2F..", "x_y.png.exe.sh"])
def test_bad_names_never_reach_the_disk(orch, name):
    st, _, body = _call("GET", f"/upload/{name}")
    assert st in (400, 404)
    assert b"TOP SECRET" not in body


def test_a_symlink_out_of_uploads_is_refused(orch):
    (orch / "state" / "uploads" / "1791529009_lnk001.txt").symlink_to(orch / "state" / "secret.txt")
    st, _, body = _call("GET", "/upload/1791529009_lnk001.txt")
    assert st == 404 and b"TOP SECRET" not in body


# ---- (c) missing = plain 404 --------------------------------------------------------------------

def test_missing_file_is_404(orch):
    assert _call("GET", "/upload/1791529999_gone00.jpg")[0] == 404


# ---- (d) immutable + cacheable ------------------------------------------------------------------

def test_cache_headers_and_conditional_get(orch):
    st, h, _ = _call("GET", f"/upload/{IMG}")
    assert "immutable" in h["Cache-Control"] and "max-age=31536000" in h["Cache-Control"]
    assert h.get("ETag") and h.get("Last-Modified")
    st2, _, body2 = _call("GET", f"/upload/{IMG}", headers={"If-None-Match": h["ETag"]})
    assert st2 == 304 and body2 == b""


# ---- (f) Range ----------------------------------------------------------------------------------

def test_range_request_is_206(orch):
    data = (orch / "state" / "uploads" / IMG).read_bytes()
    st, h, body = _call("GET", f"/upload/{IMG}", headers={"Range": "bytes=10-19"})
    assert st == 206 and body == data[10:20]
    assert h["Content-Range"] == f"bytes 10-19/{len(data)}"


# ---- (e) thumbnails -----------------------------------------------------------------------------

def test_image_thumb_is_jpeg_long_edge_400(orch):
    from PIL import Image
    st, h, body = _call("GET", f"/upload/{IMG}?thumb=1")
    assert st == 200 and h["Content-Type"] == "image/jpeg"
    im = Image.open(io.BytesIO(body))
    assert im.format == "JPEG" and max(im.size) == 400 and im.size == (400, 200)


def test_thumb_of_a_non_media_file_is_404(orch):
    assert _call("GET", f"/upload/{HTML}?thumb=1")[0] == 404


def test_thumb_of_an_undecodable_image_is_404_not_a_placeholder(orch):
    (orch / "state" / "uploads" / "1791529010_bad001.jpg").write_bytes(b"not an image")
    assert _call("GET", "/upload/1791529010_bad001.jpg?thumb=1")[0] == 404


def test_thumb_never_writes_into_uploads(orch):
    before = sorted(p.name for p in (orch / "state" / "uploads").iterdir())
    _call("GET", f"/upload/{IMG}?thumb=1")
    assert sorted(p.name for p in (orch / "state" / "uploads").iterdir()) == before


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")
def test_video_poster_frame(orch):
    from PIL import Image
    out = orch / "state" / "uploads" / VID
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10",
                    "-t", "2", "-pix_fmt", "yuv420p", str(out)], check=True)
    st, h, body = _call("GET", f"/upload/{VID}?thumb=1")
    assert st == 200 and h["Content-Type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(body)).size) == 400
    st, h, _ = _call("GET", f"/upload/{VID}")
    assert h["Content-Type"] == "video/mp4"


def test_a_file_in_uploads_with_a_non_api_name_is_refused(orch):
    """The allowlist is its own fence: hand-placed files (proof dumps, frame grabs, notes) stay private."""
    (orch / "state" / "uploads" / "notes.txt").write_text("hand placed")
    st, _, body = _call("GET", "/upload/notes.txt")
    assert st == 404 and b"hand placed" not in body


# ---- gm's conditions on DEC-1791529672497289 ----------------------------------------------------

@pytest.mark.parametrize("name", [".1791529000_abc123.png", "%2F1791529000_abc123.png"])
def test_leading_dot_and_slash_are_refused(orch, name):
    (orch / "state" / "uploads" / ".1791529000_abc123.png").write_bytes(_png())
    assert _call("GET", f"/upload/{name}")[0] == 404


def test_a_trailing_newline_in_the_name_is_refused(orch):
    """Python's $ also matches before a final newline: the name rule must use fullmatch (macos-dev,
    #338 review). Worst case is a pre-existing file literally named with the newline, so plant one."""
    (orch / "state" / "uploads" / "1791529000_nl0001.png\n").write_bytes(_png())
    st, _, body = _call("GET", "/upload/1791529000_nl0001.png%0A")
    assert st == 404 and not body.startswith(b"\x89PNG")


def test_every_symlink_is_refused_even_one_pointing_at_another_upload(orch):
    (orch / "state" / "uploads" / "1791529011_lnk002.png").symlink_to(orch / "state" / "uploads" / IMG)
    assert _call("GET", "/upload/1791529011_lnk002.png")[0] == 404


def test_an_image_name_with_non_image_bytes_is_an_attachment(orch):
    """Content-Type is not taken from the extension alone: the bytes must agree."""
    (orch / "state" / "uploads" / "1791529012_fake01.png").write_text("<html><script>x</script>")
    st, h, _ = _call("GET", "/upload/1791529012_fake01.png")
    assert st == 200 and h["Content-Type"] == "application/octet-stream"
    assert h["Content-Disposition"].startswith("attachment")


def test_a_real_video_and_audio_are_inline(orch):
    (orch / "state" / "uploads" / "1791529013_aud001.mp3").write_bytes(b"ID3\x04\x00" + b"\x00" * 64)
    st, h, _ = _call("GET", "/upload/1791529013_aud001.mp3")
    assert h["Content-Type"] == "audio/mpeg"


def test_over_the_size_cap_is_413(orch, monkeypatch):
    monkeypatch.setattr(G, "UPLOAD_MAX_BYTES", 100)
    assert _call("GET", f"/upload/{IMG}")[0] == 413


def test_every_request_is_logged_without_the_token(orch, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="watch_gateway"):
        _call("GET", f"/upload/{IMG}")
        _call("GET", "/upload/1791529999_gone00.jpg")
    lines = [r.getMessage() for r in caplog.records if "upload-get" in r.getMessage()]
    assert len(lines) == 2
    assert all("Bearer" not in l and "read" not in l.split("principal=")[0] for l in lines)
    assert "dev-read" in lines[0] and IMG in lines[0] and "404" in lines[1]


def test_png_bytes_named_jpg_are_inline_as_png(orch):
    """Measured 10-09: 48 of 72 real .jpg uploads are PNG bytes. They must still preview."""
    (orch / "state" / "uploads" / "1791529014_pngjpg.jpg").write_bytes(_png())
    st, h, _ = _call("GET", "/upload/1791529014_pngjpg.jpg")
    assert st == 200 and h["Content-Type"] == "image/png" and "Content-Disposition" not in h


def test_video_bytes_named_png_are_an_attachment(orch):
    """Same family only: a .png name never serves video bytes inline."""
    (orch / "state" / "uploads" / "1791529015_vidpng.png").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
    st, h, _ = _call("GET", "/upload/1791529015_vidpng.png")
    assert h["Content-Type"] == "application/octet-stream"


# ---- the app-store review conditions, by NAME --------------------------------------------------

def _jpeg_with_gps():
    from PIL import Image
    im = Image.new("RGB", (1000, 800), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "Apple"                                  # Make
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (12.0, 34.0, 56.0), "W", (65.0, 43.0, 21.0)   # a made-up point
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif)
    return b.getvalue()


def test_exif_strip_image_thumb_carries_no_exif_or_gps(orch):
    from PIL import Image
    src = _jpeg_with_gps()
    assert Image.open(io.BytesIO(src)).getexif().get_ifd(0x8825)      # the fixture really has GPS
    (orch / "state" / "uploads" / "1791529016_gps001.jpg").write_bytes(src)
    st, _, body = _call("GET", "/upload/1791529016_gps001.jpg?thumb=1")
    assert st == 200
    im = Image.open(io.BytesIO(body))
    assert len(im.getexif()) == 0 and "exif" not in im.info
    assert b"Exif\x00\x00" not in body


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")
def test_exif_strip_video_poster_carries_no_metadata(orch):
    from PIL import Image
    out = orch / "state" / "uploads" / "1791529017_vidgps.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "2",
                    "-metadata", "location=+12.5822-065.7225/", "-pix_fmt", "yuv420p", str(out)], check=True)
    st, _, body = _call("GET", "/upload/1791529017_vidgps.mp4?thumb=1")
    assert st == 200
    im = Image.open(io.BytesIO(body))
    assert len(im.getexif()) == 0 and b"Exif\x00\x00" not in body and b"+12.5822" not in body


def test_capability_gate_upload_fetch_is_advertised(orch, monkeypatch):
    import json
    monkeypatch.setattr(G, "_capability_providers", lambda: [])
    monkeypatch.setattr(G, "_pending_count", lambda: 0)
    st, _, body = _call("GET", "/gateway/capabilities")
    assert st == 200 and "upload_fetch" in json.loads(body)["features"]


@pytest.mark.parametrize("path", ["/upload", "/upload/", "/upload/*", "/upload/%2A"])
def test_never_list_or_enumerate_uploads(orch, path):
    st, _, body = _call("GET", path)
    assert st in (404, 405)
    assert IMG.encode() not in body and HTML.encode() not in body


@pytest.mark.parametrize("path", ["/upload/1791529000_nothere.png", "/upload/..%2Fsecret.txt",
                                  "/upload/1791529000_x.png?thumb=1"])
def test_refusals_carry_the_sandbox_headers_and_are_never_cached(orch, path):
    """Every answer, refusals included, goes out under the same CSP sandbox + nosniff (review of #338)."""
    st, h, _ = _call("GET", path)
    assert st == 404
    assert h["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Cache-Control"] == "private, no-store"


def test_over_the_cap_refusal_carries_the_headers_too(orch, monkeypatch):
    monkeypatch.setattr(G, "UPLOAD_MAX_BYTES", 100)
    st, h, _ = _call("GET", f"/upload/{IMG}")
    assert st == 413 and h["Content-Security-Policy"] == "default-src 'none'; sandbox"
