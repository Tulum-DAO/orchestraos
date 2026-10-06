# RFC 0001 — Repository topology: one core, clients out, and the drift that matters more

**Status:** proposed, awaiting the operator's decision
**Author:** orchestraos-builder (gen 11); §3 re-measured, §3.1 and the
bidirectionality finding added by gen 12 on promotion
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

**Re-measured independently by gen 12 on promotion (2026-10-06 02:5xZ).** Method:
`git ls-files '*.py'` in each tree, md5 per shared path — stated because gen 11's walk
included vendored packages and its totals were low. **Every number moved in the same
direction: the drift is worse than first reported, and the 50/50 split is confirmed.**

| measure | gen 11 | **gen 12 (tracked files)** |
|---|---|---|
| shared `.py` files (public ∩ live) | 549 | **666** |
| …byte-identical | 277 (50%) | **320 (48%)** |
| …**diverged** | 272 (50%) | **346 (51%)** |
| summed absolute line delta across diverged files | 8,325 | **9,928** |
| `.py` files that exist **only live** | 504 | **935** |
| `.py` total — live vs public | 1,053 vs 584 | **1,601 vs 783** |

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

**CORRECTED 2026-10-06 — a previous revision of this section was wrong, and wrong in the
dangerous direction.** It said the second running copy was "a pinned build artifact, not an
editable tree … it cannot drift … What it needs is stopping." All three claims were false.

The second `watch_gateway.py` process (pid 156416, user `ubuntu`) runs inside the Docker
container `staging` (image `orchestraos-demo:50e9a3f`). Measured:

- **It is in service.** The container maps `18891/tcp -> 127.0.0.1:18920`, and the
  operator's staging dashboard on that port returns HTTP 200. It is **the operator's test
  environment for this repository** — verified PR branches are deployed into it for
  hands-on testing. Only its *gateway* holds no listening socket; that one fact was
  over-generalised to the whole container.
- **It drifts.** `docker diff staging` reports **11,950 changed paths in total**; **1,765**
  of them are inside the repository checkout, and **1,535** are under the five code
  directories `api/`, `dashboard/`, `scripts/`, `services/` and `orchestra_cli/` — because PR
  files are copied into the running container to test them. (The filter is stated because a
  bare count would be ambiguous; the first draft of this correction quoted only the
  five-directory figure.) The image tag says what it was *built* from, not
  what it *runs*.
- **So it is a third edited tree**, and `orchestra-builder-g72`'s original "three trees, not
  two" reading — which this section previously "corrected" — was right.

**Consequence for §6.1:** the core does not exist in two copies but in **three live ones**
(public `main`, the live fleet tree, and the staging container), plus the
`orchestraos-staging` worktree it was built from. Any single-source-of-truth plan must account
for all of them. **Do not stop the `staging` container as cleanup** — it is a working
environment, not a leftover.

**How the wrong version happened, kept as a warning:** the container was verified to *be* a
container (cgroup) and its image tag was read; "cannot drift" was then *inferred* from "has a
tag" without running `docker diff`, and "serves nothing" from one process within it. Two facts
were proven and a third was concluded. The hazard the original note named is still real, and
this is a live example of it: **confirm which process actually serves the URL, and diff the
running filesystem, before believing any file you are reading is the one in service.**

### The drift is BIDIRECTIONAL — this changes what §6.1 can safely rule

Gen 11 framed this as "live runs and is ahead; public is behind." **That is only half
true, and the other half is load-bearing.** Measured per-symbol, public is *ahead* on some
of the most important files:

| file | public | live | public-only symbols |
|---|---|---|---|
| `services/arturo/arturo-proxy.py` | 5,146 | 4,163 | **40 more defs/classes** — an entire brain-dispatch layer (`_effective_brain`, `_dispatch_guarded`, `_brain_reply`, `_default_models`, `_BrainHttpError`) |
| `scripts/watch_gateway.py` | 5,908 | 5,473 | `_identity_kwargs`, `_anchor_expect_questions`, `row_is_durable_with_parts`, `_digit_pressable_ns` |

**Consequence: "declare live canonical and copy it down" would destroy real work**,
including the provider-agnostic brain layer and the gate in §3.1 below. Neither tree
dominates, so §6.1 cannot be settled by picking a winner — it needs a *merge*, file by
file, with both directions reviewed. That is a materially bigger task than gen 11's
framing implied, and it is the main reason this RFC does not propose doing it as
ordinary builder work.

### 3.1 — A THIRD incident, found during the gen 11→12 swap, and the worst of them

**The #178 menu-identity gate is public-only. The live gateway does not have it.**

`menu_batch_submit` exists in both trees (live `:2721`, public `:2840`), but
`expect_questions` appears **14 times in public and 0 times in live** — the live signature
carries no identity parameter at all. So on the gateway that actually serves Shaw's phone
and watch, a batch anchored on durable row A can still be replayed into a *different* live
menu B and, because option digits overlap, **silently answer an agent's new question with
the operator's old intent.** No error, no mismatch signal.

The fix is reviewed, CI-green and has six dedicated tests — in the tree that serves nobody.

**Gen 11 wrote incident 1 ("a reviewer had to tell me I had fixed the wrong tree") into
this very document, and then shipped #178 public-only without noticing it had done the
same thing again.** That is the evidence that the drift is not self-correcting by
discipline: the author who had just learned the lesson, and written it down, still missed
it. Handed to `orchestra-builder` (file owner) as `msg_d0ccacc0`, live-first per gm's
re-ruling.

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

### 6.2 — ~~Make the install path true for a stranger~~ **ALREADY TRUE — measured, closed**

**Status: CLOSED by measurement, not by work.** gm authorised this as ordinary work
(sensible defaults + loud `doctor` findings) with the acceptance test *"a clean Ubuntu VM
completes `docs/INSTALL.md` unedited, proven by effect on a real throwaway VM or container,
not argued from the diff."* Gen 12 ran that test. **The premise did not survive it.**

Built the shipped `Dockerfile` from a clean `main` (`1d67746`) — which is equivalent to a
stranger's clone, since `.dockerignore` already excludes `orchestra.toml`, `state/`,
`.venv` and `node_modules` — then ran `orchestra doctor` with **no credentials and no edits
to any file**:

- **Every infrastructure row returned `OK`**: config (*"7 required keys present"*), data
  dir, registry, gateway token, all four ports free, `aiohttp`/`flask`/`openai`, all three
  `node_modules`, `better-sqlite3` and `node-pty` native bindings, api + dashboard builds,
  `rotation:beat armed`, and `hooks:claude` (12 rows).
- **`doctor` exited 1** with `4 required check(s) MISSING`, and all four were the same
  thing: *no agent CLI is logged in* (`runtime:claude` installed/`loggedIn=false`,
  `gemini`/`codex` not installed, `runtime:any`). Each carried an actionable remedy.

**That is the behaviour §6.2 asked for, already present.** It fails loudly and precisely at
the one boundary no code change can cross — a stranger must log into their own CLI, and the
harness must not do it for them (the `Dockerfile` states this: *"The CLI LOGIN STAYS
YOURS"*).

**The earlier diagnosis was wrong and is retracted.** It claimed "mandatory config with
empty-string defaults, so a stranger's install comes up silently blank." Inspection shows
the host-coupled fields are *optional* config whose blank defaults are **documented
intent**: `orchestra.example.toml` says of the tailnet fields *"leave blank on a
single-machine install (B1) and the fields come back empty, not fabricated,"* and of
`vps_hostname` *"leave blank on a single-machine install."* And **no operator hostname is
hard-coded in shipped code at all** — the only match for the operator's host outside tests
and the baseline is inside `scan_operator_identifiers.py`'s own detection pattern, where it
belongs. (Naming the host here would itself have tripped that scanner, which is the gate
working: it caught this paragraph's first draft.)

Two rows that *looked* like findings are artifacts of the container, checked rather than
reported: `git:hooks WARN core.hooksPath unset` (the image has no `.git` — `.dockerignore`
excludes it; `orchestra init` *does* set `core.hooksPath=.git-hooks`, `init_cmd.py:256`,
and it is set in a real clone) and `orchestra:version INFO installed unknown` (same cause).

**Nothing to build. The authorisation was not spent.**

### 6.3 — Keep clients out, and write the contract down
No moves required. Add to each client repo's README: the HTTP endpoints it
depends on, and the §4 degradation requirement. Cheap, and it is what keeps the
boundary honest.

### 6.4 — **DEFERRED by gm, 2026-10-06. Do not reopen as idle work.**

`watch_gateway.py` is 5,908 lines holding every device's routes, the permission table, menu
parsing and the voice relay, and it is internet-reachable. It deserves a versioned surface
and a smaller file.

**gm has closed this until Shaw rules on `apr_4c6576c1`, and the reason is §3:** the right
shape for that file depends on **which merge direction is chosen**, because the two trees
disagree about the file by 435 lines and in both directions. Carving it before the merge
direction is settled would be refactoring a file that is about to be reconciled against a
different version of itself — work that the ruling could invalidate wholesale.

Recorded here explicitly so it stops reading as an open invitation.

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
2. Rule on **§6.1** — and note it is **not** "which tree wins": §3's bidirectionality
   means it is a reviewed file-by-file merge, plus the history cleanup that lets the live
   tree push. **This ask is already on your queue as card `apr_4c6576c1`** (gm, "clean the
   repo history now, keep the off-box-backup stopgap, or reconcile into public first?") —
   gen 12 deliberately did **not** file a second card; this RFC is the evidence for that
   one.
3. Authorise **§6.2** (the 7 files) as ordinary work. I can take this; it is
   small, testable, and squarely in the public repo.
4. Defer **§6.4** explicitly, so it stops being an open question.
