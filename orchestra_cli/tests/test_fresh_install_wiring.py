"""Fresh install wires the db-checkpoint beat by default."""
from pathlib import Path

from orchestra_cli import process_table as PT
from orchestra_cli import settings as S


def test_default_process_table_enables_db_checkpoint(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    data = tmp_path / "data"
    (root / "orchestra.toml").write_text(
        f'[data]\ndir = "{data}"\n'
        '[gateway]\nhost = "127.0.0.1"\nport = 8890\n'
        '[dashboard]\nhost = "127.0.0.1"\nport = 8891\n'
        '[notify]\nchannel = "none"\n[runtimes]\nenabled = ["claude"]\n'
    )
    st = S.load_settings(repo_root=root)
    assert st.db_checkpoint_enabled is True
    assert st.db_checkpoint_interval == 300
    by = {e.name: e for e in PT.build_process_table(st)}
    assert "db_checkpoint" in by
    assert by["db_checkpoint"].enabled is True
    assert by["db_checkpoint"].interval == 300
    assert by["db_checkpoint"].argv[-1].endswith("scripts/db_checkpoint.py")
