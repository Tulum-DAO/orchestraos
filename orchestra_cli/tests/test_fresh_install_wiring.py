"""Fresh `orchestra` install wires red-alert watch (report-only) by default."""
from pathlib import Path

from orchestra_cli import process_table as PT
from orchestra_cli import settings as S


def test_default_process_table_enables_report_only_watch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    data = tmp_path / "data"
    # No [red_alert] section at all — first-run defaults.
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{data}"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n'
    )
    st = S.load_settings(repo_root=root)
    assert st.red_alert_watch_enabled is True
    assert st.red_alert_repair_enabled is False
    by = {e.name: e for e in PT.build_process_table(st)}
    assert "red_alert_watch" in by
    assert by["red_alert_watch"].enabled is True
    assert by["red_alert_watch"].interval == 60
