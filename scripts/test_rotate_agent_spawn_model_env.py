

def test_spawn_successor_runs_spawn_agent_from_the_checkout_not_the_data_dir(monkeypatch, tmp_path):
    """Tier 0 item 2: under a real install ORCHESTRA_DIR is the DATA dir (no spawn-agent.sh there)."""
    import rotate_agent as RA
    seen = {}
    monkeypatch.setattr(RA, "run_cmd", lambda cmd, env=None, **k: seen.update(cmd=cmd), raising=False)
    monkeypatch.setattr(RA, "ORCHESTRA_DIR", tmp_path / "data", raising=False)
    RA._spawn_successor("seat-g2", "m", "claude")
    assert seen["cmd"][0] == str(RA.CODE_ROOT / "spawn-agent.sh")
    assert str(tmp_path) not in seen["cmd"][0]
