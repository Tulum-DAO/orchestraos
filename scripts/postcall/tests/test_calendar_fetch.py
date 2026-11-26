"""The calendar feed (design §3): the secret ICS URL is read by reference, fetched read-only, and
written atomically to the local copy calendar_ics.load reads. The URL never appears in any output."""
import json
import os
import stat

from postcall import calendar_fetch

URL = "https://calendar.example.invalid/private-0123456789abcdef/basic.ics"
ICS = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def _paths(tmp_path, url=URL):
    url_file = tmp_path / "secrets" / "cal.url"
    url_file.parent.mkdir()
    if url is not None:
        url_file.write_text(url + "\n")
    return str(url_file), str(tmp_path / "cal" / "shaw.ics")


def _opener(body, calls):
    def op(url, timeout):
        calls.append((url, timeout))
        return body
    return op


def test_no_url_file_is_a_no_op(tmp_path):
    url_file, out = _paths(tmp_path, url=None)
    calls = []
    assert calls == [] and calendar_fetch.refresh(url_file, out, opener=_opener(ICS, calls)) == "no-url"
    assert not os.path.exists(out)


def test_fetches_and_writes_the_copy_0600(tmp_path):
    url_file, out = _paths(tmp_path)
    calls = []
    assert calendar_fetch.refresh(url_file, out, opener=_opener(ICS, calls), now=1000.0) == "fetched"
    assert calls[0][0] == URL and open(out, newline="").read() == ICS
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_a_fresh_copy_is_not_refetched(tmp_path):
    url_file, out = _paths(tmp_path)
    calls = []
    calendar_fetch.refresh(url_file, out, opener=_opener(ICS, calls), now=1000.0)
    os.utime(out, (1000.0, 1000.0))
    assert calendar_fetch.refresh(url_file, out, opener=_opener(ICS, calls), now=1000.0 + 600) == "fresh"
    assert len(calls) == 1


def test_bad_content_keeps_the_old_copy_and_never_shows_the_url(tmp_path):
    url_file, out = _paths(tmp_path)
    calendar_fetch.refresh(url_file, out, opener=_opener(ICS, []), now=1000.0)
    os.utime(out, (0, 0))
    status = calendar_fetch.refresh(url_file, out, opener=_opener("<html>login</html>", []), now=1e9)
    assert status.startswith("error") and URL not in status and "private" not in status
    assert open(out, newline="").read() == ICS


def test_a_failing_fetch_never_shows_the_url(tmp_path):
    url_file, out = _paths(tmp_path)

    def boom(url, timeout):
        raise OSError(f"could not reach {url}")
    status = calendar_fetch.refresh(url_file, out, opener=boom, now=1e9)
    assert status.startswith("error") and URL not in status and "private" not in status


def test_the_sweep_refreshes_the_calendar_and_reports_a_failure_without_the_url(tmp_path, monkeypatch):
    from postcall import sweep
    url_file, out = _paths(tmp_path)
    monkeypatch.setenv("POSTCALL_CALENDAR_URL_FILE", url_file)
    monkeypatch.setenv("POSTCALL_CALENDAR_FILE", out)
    seen, real = [], calendar_fetch.refresh

    def fake(url_path=None, out_path=None, **kw):
        seen.append(1)
        return real(url_path, out_path, opener=_opener("not a calendar", []), **kw)
    monkeypatch.setattr(calendar_fetch, "refresh", fake)
    root = tmp_path / "pocket"
    root.mkdir()
    res = sweep.run_once(extractor=lambda c, existing=None: None, root=str(root), mode="shadow")
    assert seen == [1] and res["calendar"].startswith("error")
    assert any(e["error"].startswith("calendar: error") for e in res["errors"])
    assert URL not in json.dumps(res)
