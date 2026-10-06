# RFC 0001 — Repository topology: one core, clients out, and the drift that matters more

**Status:** proposed, awaiting the operator's decision
**Author:** orchestraos-builder (gen 11)
**Decision owner:** Shaw, with gm
**Related:** gm's structural card `apr_4c6576c1`

---

## 0. The question asked

> Should there be a baseline OrchestraOS repo anybody can download and install
> without the fluff, with the watch app, the iOS app, the Quest app and the 3D
> brain as separate repos — or should it all be one?

## 1. Answer up front

**That model is already in place.** All four clients are already separate repos
with their own history. The public core already declares itself installable and
ships an install path. There is no monorepo to break up.

**The real problem is not topology. It is that the core itself exists in two
divergent copies, and the one that runs is not the one that is reviewed.**

Splitting more repos does not fix that. It multiplies it.

**So: keep the topology you have, do not restructure, and spend the effort on a
single source of truth for the core.**

---

## 2. What is already true (measured, 2026-10-06)

### Clients are already separate — all four, all with their own git

| repo | tracked files | last commit | toolchain |
|---|---|---|---|
| `quest-orchestra` | 94 | 2026-10-05 | Gradle / Android native |
| `orchestraos-ios` | 126 | 2026-09-18 | Xcode / Swift |
| `watch-approval-app` | 110 | 2026-09-14 | Xcode / Swift |
| `second-brain` (3D brain) | 29 | 2026-09-21 | web |

### The public core already aims to be the installable baseline

- `README.md`: *"an open harness for running a fleet of coding agents as a team…
  We are opening it so people who want this to exist can build it with us."*
- `docs/INSTALL.md`: *"One machine, one CLI, no voice, no Telegram. Target:
  gateway up, one seat spawned, one approval card answered from the web
  dashboard, in under 30 minutes on a clean Ubuntu 22.04/24.04 VPS."*
- `orchestra_cli up` — *"run gateway + api + dashboard + arturo + beats under one
  supervisor."*
- **Only 7 of 1,336 tracked files** carry host-specific coupling
  (`tailscale` / an absolute home path / the hostname), and 3 of those are tests
  or `doctor`, where it is arguably correct:
  `watch_gateway.py`, `approval_notify.py`, `approval_config.py`,
  `scan_operator_identifiers.py`, `orchestra_cli/doctor.py`,
  + 2 test files.

**The "fluff" is not leaking into the public repo.** The multi-gigabyte client
CSVs are in the *private* tree's history, which is a hygiene problem there, not a
layout problem here.

---

## 3. The finding that should change the decision

The public repo is roughly **half** of the running system, and **half of what it
does share has drifted**:

| measure | value |
|---|---|
| shared `.py` files (public ∩ live) | **549** |
| …byte-identical | 277 (50%) |
| …**diverged** | **272 (50%)** |
| summed absolute line delta across diverged files | **8,325** |
| `.py` files that exist **only live** | **504** |
| `.py` total — live vs public | **1,053 vs 584** |

Named examples, because the aggregate hides the shape:

| file | public | live |
|---|---|---|
| `lineage_daemon/wal/real_seams.py` | 100 | 260 |
| `lineage_daemon/wal/bg_beat.py` | 1,244 | 1,681 |
| `identity_store/projector.py` | 654 | 858 |
| `agent-status.py` | 1,607 | 1,792 |
| `watch_gateway.py` route-scope entries | 60 | 53 |
| `_complete_unobservable` | **does not exist** | exists |

There is also a **third** tree: `orchestraos-staging`, 1,233 tracked files.

### This has already cost real money, twice, today

1. **I fixed the rotation deadlock in the public repo and the fleet stayed
   broken.** PR #177 corrected a tmux target that made the engine record the
   wrong process id — the root cause of agents needing manual rotation. The live
   recorder was untouched, so nothing changed in production until it was ported
   separately. **A reviewer had to tell me I had fixed the wrong tree.**
2. **I nearly reported a defect that did not exist**, because two copies of
   `service-watchdog.sh` disagree and only one is scheduled
   (md5 `0f08023e` runs; `3919fde7` is stale). Naming the md5 is what caught it.

Both reviewers in the related congruence round independently concluded that
"public first" is the wrong fix order **while this holds** — and gm re-ruled to
live-first because of it.

**This is the methodology problem.** Repo count is a rounding error next to it.

---

## 4. The principle worth adopting

**Split on toolchain and release cadence. Do not split on feature or on device.**

- **One core repo:** engine, gateway, API, dashboard, CLI. Shared language
  (Python/TypeScript), one CI matrix, ships together.
- **One repo per client:** different build tools (Gradle, Xcode), different
  app-store cadence, and — decisively — **they talk to the core over HTTP**,
  which is a genuine version boundary.
- **The gateway is NOT a client.** It is the core's API surface and stays in
  core. (At 5,908 lines in a single file it wants carving into a versioned
  surface, but that is a later, separate change.)

### The property that makes the client split safe, and that should be a requirement

A split is only safe if a client tolerates a core it has not caught up with.
Today's `/menu-answer` work is the proof: the Quest client calls the new route
and **falls back to the card queue on 404/403/405**, so the server could ship
before the client without a lockstep release. All four client repos already
reference those status codes (14 / 52 / 5 files).

**Proposal: make graceful degradation on 404/403/405 an explicit, stated
requirement for any client repo.** Without it, every separate repo silently
becomes a coordinated-release problem.

---

## 5. Options considered

### A. Collapse everything into one monorepo
**Rejected.** Forces Gradle, Xcode and Python/TS into one CI matrix; couples
app-store cadence to server cadence; and enlarges the public privacy surface at a
moment when operator identifiers have already leaked into the public repo twice
(#158, #175 — one of them *past a green scan*). It also would not touch the
drift.

### B. Split further — carve the gateway, engine, and dashboard into their own repos
**Rejected for now, on today's evidence.** The problem is not that the core is
too big to review; it is that there are already 2–3 copies of it. Going from two
trees to five gives five things to drift. Revisit only after §6.1 is true.

### C. Keep the current topology; fix the single-source-of-truth problem
**Recommended.** No migration, no breakage, and it attacks the defect that has
already caused two incidents in one day.

---

## 6. Recommended plan, in order

### 6.1 — One source of truth for the core *(blocks everything else)*
**Owner:** gm to assign; this is above a single builder.

The current state is that the live tree is authoritative for behaviour and the
public tree is authoritative for review, and neither is authoritative for both.
That is the thing to end. Sub-decisions that need an owner ruling, not a builder
guess:

1. **Which tree is canonical?** gm has already ruled *live-first* for per-fix
   order, which implies live is authoritative today.
2. **Why can the live tree not publish?** It cannot push a new branch at all —
   `pack exceeds maximum allowed size (2.00 GiB)`, because multi-gigabyte client
   CSVs sit in its history. **Until this is fixed, the live tree cannot
   participate in review at all, and no topology change can help.** This is the
   first concrete task.
3. **What is `orchestraos-staging` for**, now that a third copy exists?

### 6.2 — Make the install path true for a stranger *(small, ~1 day)*
Resolve the 7 host-coupled files so a clean VPS can complete `docs/INSTALL.md`
without editing source: move the host/tailnet assumptions behind config with
documented defaults, and let `orchestra_cli doctor` report them as *findings*
rather than hard-coding them. **Acceptance: a clean Ubuntu VM reaches "one
approval card answered" using only `docs/INSTALL.md`.** This is the single
highest-value step for the stated open-harness mission, and it is independent of
every other item here.

### 6.3 — Keep clients out, and write the contract down
No moves required. Add to each client repo's README: the HTTP endpoints it
depends on, and the §4 degradation requirement. Cheap, and it is what keeps the
boundary honest.

### 6.4 — *Later:* carve the gateway into a versioned API surface
`watch_gateway.py` is 5,908 lines holding every device's routes, the permission
table, menu parsing and the voice relay, and it is internet-reachable. It
deserves a versioned surface and a smaller file. **Not now** — it is the most
actively-changed file in the system and §6.1 must land first.

---

## 7. Risks and what this does not solve

- **The drift is not self-correcting.** It grew to 272 diverged files without a
  decision to let it; it will keep growing while two trees are both edited.
- **Fixing §6.2 does not make the project supportable** — it makes it
  *installable*. Those are different, and the README is honest that this is not a
  finished product.
- **A public-repo privacy surface remains.** Two leaks have already reached it,
  one past a green scan. Keeping fleet specifics out of core is an ongoing cost,
  not a one-time fix.
- **This RFC proposes no code change.** That is deliberate: the measurement is
  the deliverable, and the decision is the operator's.

---

## 8. What I am asking for

1. Confirm **topology stays as-is** (core + four client repos). No restructure.
2. Rule on **§6.1** — which tree is canonical, and authorise the history
   cleanup that lets the live tree push.
3. Authorise **§6.2** (the 7 files) as ordinary work. I can take this; it is
   small, testable, and squarely in the public repo.
4. Defer **§6.4** explicitly, so it stops being an open question.
