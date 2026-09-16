"""orchestra — the OrchestraOS install/doctor/supervisor CLI (stdlib only).

    orchestra init     create the data dir, orchestra.toml, venv, npm installs, builds
    orchestra doctor   check CLIs/auth, ports, tmux, config keys, builds, rotation beat
    orchestra up       run gateway + api + dashboard + arturo + the beats under one supervisor
    orchestra down     stop a running supervisor
    orchestra status   show what the supervisor is running

Everything comes from orchestra.toml (path: $ORCHESTRA_CONFIG, else <repo>/orchestra.toml)
plus a few env vars; there are no operator-specific literals in this package.
"""
