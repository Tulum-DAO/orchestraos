"""seat_identity — is caller the same SEAT as a card's author, across rotations?

Card annotation (append_note) used to compare literal ids, so after a rotation a seat could not
annotate its own predecessor's cards (ios-watch-dev g21 vs a card by ios-watch-dev-g18;
orchestraos-builder msg_e4def9c1).

POSITIVE EVIDENCE only. `<root>-gN` maps to `root` iff the registry has generations(root, N); a name
prefix proves nothing (ios-watchdog, ios-watch-dev-x, ios-watch-dev-g99 do not map). The caller must
also be the root itself or that root's CURRENT canonical generation; a stale generation is refused.
Any registry failure falls back to literal equality (fail closed: no new access on doubt).
"""
import os
import re
import sqlite3

# `-gN` and the older `-genN` (orchestra-builder-gen53, gm msg_65115ef3). Evidence is still required.
_GEN = re.compile(r"^(?P<root>.+)-g(?:en)?(?P<n>\d+)$")


def registry_beside(db_path):
    """The registry lives next to tasks.db in state/; a tmp store finds only a tmp registry."""
    return os.path.join(os.path.dirname(os.path.abspath(str(db_path))), "orchestra-registry.db")


def _conn(reg):
    if not reg or not os.path.isfile(reg):
        return None
    return sqlite3.connect(f"file:{reg}?mode=ro", uri=True, timeout=5)


def resolve(reg, name):
    """(root, generation-or-None). `name` itself when there is no positive evidence."""
    m = _GEN.match(name or "")
    if not m:
        return name, None
    c = _conn(reg)
    if c is None:
        return name, None
    try:
        hit = c.execute("SELECT 1 FROM generations WHERE root=? AND generation=?",
                        (m["root"], int(m["n"]))).fetchone()
    finally:
        c.close()
    return (m["root"], int(m["n"])) if hit else (name, None)


def current_generation(reg, root):
    c = _conn(reg)
    if c is None:
        return None
    try:
        row = c.execute("SELECT g.root, g.generation FROM canonical c JOIN generations g "
                        "ON g.id = c.generation_id WHERE c.root=?", (root,)).fetchone()
    finally:
        c.close()
    if not row:
        return None
    g_root, g_n = row
    if g_root == root:
        return g_n
    m = _GEN.match(g_root)          # canonical may point at a `<root>-gN` alias row
    return int(m["n"]) if m and m["root"] == root else None


def live_root(reg, name):
    """The seat `name` belongs to (by evidence) if that seat has a canonical row with status
    'online', else None: retired, parked, unknown, or not a seat at all."""
    root, _gen = resolve(reg, name or "")
    c = _conn(reg)
    if c is None:
        return None
    try:
        row = c.execute("SELECT status FROM canonical WHERE root=?", (root,)).fetchone()
    finally:
        c.close()
    return root if row and row[0] == "online" else None


STEWARD = "gm"     # orphan steward: may annotate cards whose author has no live seat (gm msg_65115ef3)


def _is_current(reg, caller, root):
    """caller is `root` itself or root's CURRENT canonical generation."""
    c_root, c_gen = resolve(reg, caller)
    if c_root != root:
        return False
    if c_gen is None:
        return caller == root
    return c_gen == current_generation(reg, root)


def may_retire(reg, caller, author):
    """(ok, reason) for the card RETIRE verb (operator ruling 2026-10-10, gm msg_3f3a4d05): the author, the
    author's bare root or CURRENT canonical generation, or gm (bare or current generation) on any card.
    Narrower than may_annotate: no orphan-steward branch (gm already covers it) and no 'operator' (their
    in-app dismiss stays 'discarded')."""
    try:
        if _is_current(reg, caller, STEWARD):
            return True, None
    except sqlite3.Error:
        pass
    ok, why = may_annotate(reg, caller, author)
    if ok and why != "orphan-steward":
        return True, None
    return False, why if (why and why != "orphan-steward") else (
        f"{caller} may not retire a card authored by {author}: only the author, its current "
        f"generation, or gm may retire it")


def may_annotate(reg, caller, author):
    """(ok, reason). ok when caller IS author, or caller is the same seat by positive evidence AND
    is the bare root or its current canonical generation. The operator is handled by the caller."""
    if caller == author:
        return True, None
    try:
        c_root, c_gen = resolve(reg, caller)
        a_root, _ = resolve(reg, author)
        if c_root != a_root:
            # ORPHAN STEWARD: gm (bare or its current generation) may annotate a card whose author
            # has NO live seat. Notes only; never answer, dismiss or expire. card_hygiene routes
            # exactly these orphans to gm.
            if _is_current(reg, caller, STEWARD) and live_root(reg, author) is None:
                return True, "orphan-steward"
            return False, None
        if c_gen is None and caller == c_root:
            return True, None
        cur = current_generation(reg, c_root)
        if c_gen is not None and cur is not None and c_gen == cur:
            return True, None
        return False, (f"{caller} is a generation of {c_root} but not its current canonical "
                       f"generation (g{cur})")
    except sqlite3.Error:
        return False, None
