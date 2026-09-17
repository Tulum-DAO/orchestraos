<!-- Thanks! Keep it short. The "Proven by effect" section is required. -->

## What and why

<!-- One paragraph: the problem, the change, the issue it closes (Closes #…). -->

## RED first

<!-- Which test was red before this change, and why it failed for the right reason.
     Paste the failing assertion or the one-line pytest summary. -->

## Proven by effect

<!-- What you RAN against a live install and what you observed. Examples:
     - `orchestra up --detach` → `orchestra status` shows api running, restarts=0
     - spawned seat `hello` → `/api/agents` shows status idle, detector_age_ms 1
     - card apr_… approved via `POST /api/approvals/<id>/approve` → `approval.py get` status resumed
     A test passing is not a by-effect line. -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`, DCO)
- [ ] Per-package tests green locally (`make test`), api typecheck, dashboard build
- [ ] No operator literals or credentials (paths, hostnames, chat ids, keys) added
- [ ] Code paths resolve from the checkout; only data goes under `ORCHESTRA_DIR`
- [ ] Docs updated if behavior or the install path changed
