from services.arturo import gateway_conf as gc


def test_agent_message_url_targets_local_gateway():
    assert gc.AGENT_MESSAGE_URL == "http://127.0.0.1:9091/agent-message"


def test_token_reads_gateway_token_file(monkeypatch, tmp_path):
    # B1: MUST match watch_gateway.py's source — a FILE, not an env var / .env.secrets.
    tf = tmp_path / "watch-gateway-token"
    tf.write_text("tkn123\n")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(tf))
    assert gc.token() == "tkn123"
