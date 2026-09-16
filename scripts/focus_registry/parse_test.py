"""Tests for the fleet-audit -> focus-entity parser (WS1)."""
from scripts.focus_registry.parse import parse_audit

_SNIPPET = """# Fleet Audit

## FOCUS CLUSTERS

### ACME-APP

| Focus | Agents | %done | Rel | Imp | Notes |
|---|---|---|---|---|---|
| **Custom-intent audiences** | opus-10 (cull), **opus-11 (KEEP)**, **merge-9 (KEEP)** | 80% | H | H | Live feature. opus-11 mid-debug. |
| **Billing** | billing-architecture (cull), billing-page-build (cull) | 100% | M | H | Ladder landed. |

### BUSINESS

| Focus | Agents | %done | Rel | Imp | Notes |
|---|---|---|---|---|---|
| **AEO capability** | **aeo-expert (KEEP)** | 100% | M | M | Standing resident. |
"""


def test_parses_one_focus_per_table_row():
    focuses = parse_audit(_SNIPPET)
    names = {f.canonical for f in focuses}
    assert names == {"Custom-intent audiences", "Billing", "AEO capability"}


def test_extracts_scores_and_category():
    focuses = {f.canonical: f for f in parse_audit(_SNIPPET)}
    ci = focuses["Custom-intent audiences"]
    assert ci.category == "ACME-APP"
    assert ci.pct_done == 80
    assert ci.relevance == "H"
    assert ci.importance == "H"
    aeo = focuses["AEO capability"]
    assert aeo.category == "BUSINESS"
    assert aeo.pct_done == 100


def test_owner_is_the_keep_flagged_agent():
    focuses = {f.canonical: f for f in parse_audit(_SNIPPET)}
    # opus-11 and merge-9 both KEEP; owner is the first KEEP agent.
    assert focuses["Custom-intent audiences"].owner == "agent:opus-11"
    assert focuses["AEO capability"].owner == "agent:aeo-expert"


def test_all_member_agents_captured():
    focuses = {f.canonical: f for f in parse_audit(_SNIPPET)}
    ci = focuses["Custom-intent audiences"]
    assert ci.agents == ["agent:opus-10", "agent:opus-11", "agent:merge-9"]


def test_focus_with_no_keep_agent_has_no_owner():
    focuses = {f.canonical: f for f in parse_audit(_SNIPPET)}
    # Billing has only cull agents -> no live owner.
    assert focuses["Billing"].owner is None


def test_ignores_header_and_separator_rows():
    focuses = parse_audit(_SNIPPET)
    assert all(f.canonical not in ("Focus", "") for f in focuses)


# --- regression: comma INSIDE a status annotation must not spawn garbage ids
# (ob consultant flag 2026-08-14: '(KEEP, live owner)' -> 'agent:live owner)') ---
_COMMA_IN_PARENS = """### CLIENT

| Focus | Agents | %done | Rel | Imp | Notes |
|---|---|---|---|---|---|
| **Adaptiv Payments** | pm-adaptiv-payments-5 (cull, superseded), **pm-adaptiv-payments-6 (KEEP, live owner)** | 60% | H | H | live. |
| **Pocket** | **pocket-agent (KEEP, always_on 86%->handoff)** | 70% | M | M | live. |
"""


def test_comma_inside_parens_does_not_create_garbage_agent_ids():
    focuses = {f.canonical: f for f in parse_audit(_COMMA_IN_PARENS)}
    adaptiv = focuses["Adaptiv Payments"]
    assert adaptiv.agents == ["agent:pm-adaptiv-payments-5", "agent:pm-adaptiv-payments-6"]
    # no id ending in ')' or containing a status word
    assert not any(a.endswith(")") or " " in a for a in adaptiv.agents)


def test_comma_in_parens_owner_still_resolves_to_real_name():
    focuses = {f.canonical: f for f in parse_audit(_COMMA_IN_PARENS)}
    assert focuses["Adaptiv Payments"].owner == "agent:pm-adaptiv-payments-6"
    assert focuses["Pocket"].agents == ["agent:pocket-agent"]
    assert focuses["Pocket"].owner == "agent:pocket-agent"


# --- regression: a free-text agent entry with no parens ('name Stage-2 overlap')
# must yield the leading token only — real agent ids never contain spaces. ---
_FREETEXT_AGENT = """### ACME-APP

| Focus | Agents | %done | Rel | Imp | Notes |
|---|---|---|---|---|---|
| **Custom-intent audiences** | **opus-11 (KEEP)**, client-page-access Stage-2 overlap | 80% | H | H | live. |
"""


def test_freetext_agent_note_yields_leading_token_only():
    f = {x.canonical: x for x in parse_audit(_FREETEXT_AGENT)}["Custom-intent audiences"]
    assert f.agents == ["agent:opus-11", "agent:client-page-access"]
    assert not any(" " in a for a in f.agents)
