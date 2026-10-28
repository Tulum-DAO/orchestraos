"""A worker spawn never holds the operator's turn for all of it: a spawn that finishes within the window answers
as before; one still booting once its session is live answers "starting", and the boot check, the commission row
and the registration check finish in the background and report into the conversation as a job."""
import threading
import time

from services.arturo.test_commission import mod  # noqa: F401  (the same fixture: fleet turn, fenced data dir)


def _stubs(mod, monkeypatch, run, live=lambda: True):
    sent = []

    class _Store:
        def send(self, **kw):
            sent.append(kw)
            return "msg_bg1"
    monkeypatch.setattr(mod, "_run_commission", run)
    monkeypatch.setattr(mod, "_message_store", lambda: _Store())
    monkeypatch.setattr(mod, "run_local", lambda cmd, timeout=15, **_k: (live(), ""))
    monkeypatch.setattr(mod, "record_spawned_session", lambda s: None)
    monkeypatch.setattr(mod, "_notify_spawned", lambda *a, **k: None)
    monkeypatch.setattr(mod, "registration_status",
                        lambda name: {"state": "registered", "registered": True, "where": "registry.json", "detail": ""})
    return sent


def test_a_slow_boot_answers_starting_and_reports_later_as_a_job(mod, monkeypatch):
    release = threading.Event()
    sent = _stubs(mod, monkeypatch, lambda plan, timeout: (release.wait(5), (True, "[spawn] ready"))[1])
    jobs = []

    class _Jobs:
        def start(self, thread, kind, title, work, cancel=None):
            jobs.append({"title": title, "work": work})
            return {"id": "job_x"}
    monkeypatch.setattr(mod, "_JOBS", _Jobs())
    monkeypatch.setattr(mod, "_current_thread", lambda: "web_demo")
    t0 = time.time()
    out = mod.execute_tool("spawn_agent", {"session_name": "docs-dev", "machine": "vps", "task": "write the README"})
    assert time.time() - t0 < 3                                          # the turn did not wait for the boot
    assert out.startswith("'docs-dev' is starting on vps: its session is live")
    assert jobs and jobs[0]["title"] == "Starting docs-dev" and sent == []   # the row waits for the boot to finish
    release.set()
    text, ok = jobs[0]["work"](None)
    assert ok and "CONFIRMED" in text and sent and sent[0]["to_agent"] == "docs-dev"


def test_a_fast_spawn_answers_exactly_as_before(mod, monkeypatch):
    sent = _stubs(mod, monkeypatch, lambda plan, timeout: (True, "[spawn] ready"))
    out = mod.execute_tool("spawn_agent", {"session_name": "docs-dev", "machine": "vps", "task": "write the README"})
    assert out.startswith("CONFIRMED: Agent 'docs-dev' is running") and sent


def test_a_failed_spawn_in_the_background_reports_the_failure(mod, monkeypatch):
    release = threading.Event()
    _stubs(mod, monkeypatch, lambda plan, timeout: (release.wait(5), (False, "boot inject failed"))[1])
    jobs = []
    monkeypatch.setattr(mod, "_JOBS", type("J", (), {"start": lambda self, *a, **k: jobs.append(a[3]) or {"id": "j"}})())
    monkeypatch.setattr(mod, "_current_thread", lambda: "web_demo")
    mod.execute_tool("spawn_agent", {"session_name": "docs-dev", "machine": "vps", "task": "x"})
    release.set()
    text, ok = jobs[0](None)
    assert not ok and text.startswith("FAILED to spawn 'docs-dev' on vps: boot inject failed")
