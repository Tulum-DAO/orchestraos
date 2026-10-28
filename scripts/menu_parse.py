"""menu_parse.py — parse an approval row's stored `menu` column (the bridge's pane-menu capture).

Its own module, with nothing but json, so a reader that only needs the parse (approval.py pending, which
the dashboard runs on every notifications poll) does not import watch_gateway: that import loads the whole
gateway and, through it, the voice bridge and Arturo, on every call. watch_gateway re-exports it as _menu,
so both transports parse a menu row identically.
"""
import json


def parse_menu(r):
    """Parse the menu JSON col (app-watch-decision-surface §1) — the bridge's
    stored pane-menu capture, passed verbatim. None for non-menu rows."""
    m = r.get("menu")
    if isinstance(m, str) and m:
        try:
            m = json.loads(m)
        except ValueError:
            return None
    if isinstance(m, dict):
        if "options" in m and isinstance(m["options"], list):
            cleaned = []
            for i, opt in enumerate(m["options"]):
                if isinstance(opt, dict):
                    opt_c = dict(opt)
                    if "n" in opt_c:
                        opt_c["n"] = str(opt_c["n"])
                    else:
                        opt_c["n"] = str(i + 1)
                    if "input_kind" not in opt_c:
                        opt_c["input_kind"] = "direct"
                    cleaned.append(opt_c)
                elif isinstance(opt, str):
                    cleaned.append({"n": str(i + 1), "label": opt, "input_kind": "direct"})
            m["options"] = cleaned
        return m
    return None
