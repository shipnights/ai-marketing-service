"""/uploads static mount & STORAGE_DRIVER tests (gap #14).

In the test suite STORAGE_DRIVER=local and MEDIA_LOCAL_PATH points at a temp
dir (conftest). Driver variants are booted in a subprocess because the mount
is decided at import time.
"""

import os
from pathlib import Path

import pytest

from backend.fixtures.subprocess_app import run_app


@pytest.fixture
def media_dir():
    from app.config import MEDIA_LOCAL_PATH

    return Path(MEDIA_LOCAL_PATH)


@pytest.fixture
def media_file(media_dir):
    path = media_dir / "proj-1" / "hero.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
    yield path
    path.unlink(missing_ok=True)


# -- local driver (in-process) --------------------------------------------------------------

def test_uploaded_file_is_served_with_content_type(app_client, media_file):
    r = app_client.get("/uploads/proj-1/hero.png")
    assert r.status_code == 200
    assert r.content == media_file.read_bytes()
    assert r.headers["content-type"] == "image/png"


def test_media_is_public_no_auth_or_api_key_needed(app_client, media_file, monkeypatch):
    """Social platforms fetch media by URL (MEDIA_BASE_URL), so /uploads must be
    reachable without auth - and without the API key, which only guards /api/v1."""
    import app.middleware as mw

    monkeypatch.setattr(mw, "REQUIRE_API_KEY", True)
    monkeypatch.setattr(mw, "API_KEY", "k")
    assert app_client.get("/uploads/proj-1/hero.png").status_code == 200


def test_missing_file_404(app_client):
    assert app_client.get("/uploads/proj-1/nope.png").status_code == 404


def test_no_directory_listing(app_client, media_file):
    assert app_client.get("/uploads/").status_code == 404
    assert app_client.get("/uploads/proj-1/").status_code == 404


@pytest.mark.parametrize("path", [
    "/uploads/../conftest.py",
    "/uploads/%2e%2e/%2e%2e/etc/passwd",
    "/uploads/..%2f..%2fetc%2fpasswd",
    "/uploads/proj-1/../../secret.txt",
])
def test_path_traversal_blocked(app_client, media_dir, path):
    secret = media_dir.parent / "secret.txt"
    secret.write_text("TOP-SECRET")
    try:
        r = app_client.get(path)
        assert r.status_code == 404
        assert "TOP-SECRET" not in r.text
    finally:
        secret.unlink(missing_ok=True)


def test_static_files_get_security_headers(app_client, media_file):
    """nosniff matters most for user-controlled files: it stops a browser from
    executing an 'image' that is really HTML/JS."""
    r = app_client.get("/uploads/proj-1/hero.png")
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: /uploads serves any file type with its natural content type. An "
    "uploaded .html/.svg is rendered inline on the API origin (stored XSS vector "
    "once user uploads land here). Media should be served with "
    "Content-Disposition: attachment or a strict CSP / allow-list of image/video types."))
def test_uploaded_html_is_not_rendered_inline(app_client, media_dir):
    page = media_dir / "evil.html"
    page.write_text("<script>alert(document.cookie)</script>")
    try:
        r = app_client.get("/uploads/evil.html")
        ctype = r.headers.get("content-type", "")
        assert not ctype.startswith("text/html") or "attachment" in r.headers.get("content-disposition", "")
    finally:
        page.unlink(missing_ok=True)


# -- drivers (subprocess) -------------------------------------------------------------------------

def test_local_driver_creates_missing_media_dir(tmp_path):
    target = tmp_path / "deep" / "nested" / "uploads"
    result = run_app("""
        import os
        from app.config import MEDIA_LOCAL_PATH
        with TestClient(app) as c:                 # also runs the lifespan hook
            result = [os.path.isdir(MEDIA_LOCAL_PATH), c.get("/health").status_code]
    """, env={"STORAGE_DRIVER": "local", "MEDIA_LOCAL_PATH": str(target)})
    assert result == [True, 200]


@pytest.mark.parametrize("driver", ["s3", "r2"])
def test_remote_drivers_start_without_local_mount(tmp_path, driver):
    target = tmp_path / "must-not-exist"
    result = run_app("""
        with TestClient(app) as c:
            result = [c.get("/health").status_code, c.get("/uploads/x.png").status_code,
                      any(getattr(r, "path", "") == "/uploads" for r in app.routes)]
    """, env={"STORAGE_DRIVER": driver, "MEDIA_LOCAL_PATH": str(target)})
    assert result == [200, 404, False]
    assert not target.exists()                     # no stray local directory


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: STORAGE_DRIVER is not validated. A typo such as 'locla' silently "
    "disables the /uploads mount and the app starts 'healthy', so media URLs "
    "404 in production with no startup error. Unknown drivers should fail fast."))
def test_unknown_storage_driver_fails_fast(tmp_path):
    with pytest.raises(RuntimeError):
        run_app("result = True", env={"STORAGE_DRIVER": "locla", "MEDIA_LOCAL_PATH": str(tmp_path / "m")})
