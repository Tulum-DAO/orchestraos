# Code taken from Nod

OrchestraOS takes a few components from [Nod](https://github.com/n8n-io/nod) (n8n GmbH, Apache-2.0). This folder holds the rules and the record for that code. It does not hold the code itself: an imported file lives where it is used, for example a dashboard component under `dashboard/src`.

Nod is a research preview that changes daily, and it ships copyleft parts. So every import is **tracked**, **licensed** and kept **replaceable**. `check_imports.py` enforces all three, and CI runs it through `test_check_imports.py`.

## Importing a file
1. Copy the file from a pinned Nod commit, never from `main` without a sha.
2. Put this header in its first 15 lines, in the file's comment style:
   ```
   Upstream: n8n-io/nod <upstream path> @ <40-hex sha> (Apache-2.0), modified: yes|no
   ```
   Apache-2.0 §4(b) requires a modified file to say that it was changed. `modified: yes` does that.
3. Add one line to `IMPORTS.jsonl`:
   ```json
   {"local": "dashboard/src/components/SandboxedFrame.tsx", "upstream": "src/web/components/attachment-frame.tsx",
    "upstream_sha": "<40-hex>", "verdict": "ADAPT", "modified": true, "license": "Apache-2.0",
    "take_list_id": "T2", "local_deps": ["dashboard/src/lib/utils.ts"]}
   ```
   `verdict` is `TAKE` (as is) or `ADAPT` (changed). `take_list_id` is the approved row in the Nod take list. Nothing is imported without an approved row. `local_deps` names every file in this repository that the import may use (see rule R5).
4. The first import also adds the Nod attribution to `NOTICE`.
5. Run `python3 integrations/nod/check_imports.py`. It must print `nod imports: ok`.

## The rules
| Rule | What it refuses |
|---|---|
| R1 ledger | a malformed line, an unknown verdict or take-list id, a short sha, or a license outside Apache-2.0 / MIT / BSD / ISC / 0BSD |
| R2 tree | a ledger line with no file; a file with the Nod header but no ledger line; a header that disagrees with its line |
| R3 excluded | anything from Nod's GPL speech code, its unofficial WhatsApp client, or their patches, and any import of `@spelling-creator/spellophone`, `@whiskeysockets/baileys`, `libsignal`, `@audio/decode-webm` or Pi's bundled OAuth logins (`@earendil-works/pi-ai/oauth`, `lazy-oauth`, `bun-oauth`) |
| R4 notice | a non-empty ledger without the Nod attribution in `NOTICE` |
| R5 boundary | an imported file that reaches into this repository beyond other imported files and its declared `local_deps`. Imported code stays a leaf, so replacing or dropping it touches one place |

Things that were rebuilt from Nod's ideas, rather than copied, need no ledger line. They carry no Nod code.

## Staying current with Nod
A watcher (planned) compares each ledgered file's `upstream_sha` with Nod's `main` and reports changes, security fixes and license changes. A bump is its own branch: reapply local changes, run the component's tests and this check, and update the ledger line in the same PR.
