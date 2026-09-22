# OrchestraOS — dev container / minimum-path image (checklist B3).
#
# Mirrors the clean-machine recipe the B1 outsider runs proved (ubuntu:24.04, non-root
# user with sudo, INSTALL.md §0 prerequisites, node 22, the agent CLI) so a laptop reaches
# `orchestra doctor` green without a VPS. `orchestra init` runs at build time (venv, npm
# installs, api + dashboard builds), so the first `doctor` inside the container is instant.
#
# The CLI LOGIN STAYS YOURS: nothing here copies or mounts credentials. Log in once inside
# the container (`claude`, `codex login`, `agy`) or mount your own config dir at run time
# (see docs/INSTALL.md "Dev container / Docker" — .devcontainer/devcontainer.json does the
# read-write mount of ~/.claude for you so the login persists between rebuilds).
#
#   docker build -t orchestraos .
#   docker run -it --rm -p 8891:8891 -p 8888:8888 -p 8890:8890 orchestraos
#   (inside)  claude   # log in once
#             orchestra doctor && orchestra up
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_MAJOR=22
# Which agent CLIs to preinstall (npm packages, space-separated). The runtime catalog
# probes whichever are installed AND logged in; one is enough for the minimum path.
ARG AGENT_CLIS="@anthropic-ai/claude-code"

# INSTALL.md §0 prerequisites + sudo for the non-root user
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git tmux python3 python3-venv python3-pip build-essential curl ca-certificates sudo locales iproute2 \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TERM=xterm-256color

# Agent CLI(s), global. Login happens at run time, by you.
RUN npm install -g --no-audit --no-fund ${AGENT_CLIS}

# Non-root operator with passwordless sudo (tmux seats, ports, package installs).
ARG USERNAME=orchestra
ARG UID=1000
RUN if id -u ${UID} >/dev/null 2>&1; then userdel -r "$(id -un ${UID})"; fi \
 && useradd -m -u ${UID} -s /bin/bash ${USERNAME} \
 && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
 && chmod 0440 /etc/sudoers.d/${USERNAME}

USER ${USERNAME}
WORKDIR /home/${USERNAME}/orchestraos
COPY --chown=${USERNAME}:${USERNAME} . .

# `make install` puts bin/orchestra on PATH; `orchestra init` builds everything and writes
# orchestra.toml + the data dir (~/.orchestra) for THIS user (--yes: the image's Claude
# settings are its own, so the hook rows are written without a prompt). Idempotent — re-running it
# later (postCreateCommand, or by hand) only reports "present".
ENV PATH="/home/${USERNAME}/.local/bin:${PATH}"
# Local speech-to-text is DEFAULT-ON (P1-a): the sherpa-onnx wheel is installed here; the ~99 MB
# whisper tiny.en model is fetched in the background at the first `orchestra up` inside the running
# container (never inside a request). WITH_STT=1 bakes the model into the image AND adds the opt-in
# faster-whisper engine (+~365 MB), so a demo box dictates the instant it boots.
ARG WITH_STT=0
RUN make install \
 && ORCHESTRA_SKIP_MODEL_FETCH=$([ "$WITH_STT" = "1" ] && echo 0 || echo 1) orchestra init --yes $([ "$WITH_STT" = "1" ] && echo --stt) \
 && orchestra up --dry-run

# gateway / api / dashboard
EXPOSE 8890 8888 8891

CMD ["bash", "-lc", "orchestra doctor; exec bash"]
