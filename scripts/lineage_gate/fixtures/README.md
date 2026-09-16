# Negative-fixture suite (Two-Key, Amendment 5.3)

Each fixture = one dir: `<name>/handoff.md` + `<name>/canary.json` +
`<name>/expect.json` ({"must_fail_gates": ["..."], "author": "<agent>",
"attack_class": "ghost-handoff|noise-mining|phantom-invariant|debt-omission"}).

RULE (Amendment 5.3): the Two-Key shadow exit requires fixtures authored by
NON-PROPOSERS. `author` fields are load-bearing — the suite runner marks any
proposer-authored fixture as PLACEHOLDER and the exit stays closed while only
placeholders exist. agy's set is commissioned (gm msg_28ad39b6) and must
DEFEAT the proposer placeholders where they differ.
