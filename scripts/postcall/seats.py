"""Seat resolution and the live-seat check (design §6), read-only.

  resolve(counterpart)  the `pm_agent` of the one client whose contacts name this counterpart
                        (exact, case- and whitespace-insensitive, name or alias), if that seat is
                        live; else None. An ambiguous name (two clients) is never guessed.
  is_live(seat)         the seat has an agents row with status 'online' in the registry.

Paths come from the environment at call time: POSTCALL_CLIENTS_DIR (default
~/.orchestra/state/clients) and POSTCALL_REGISTRY_FILE (default ~/.orchestra/registry.json).
Missing or malformed files mean "no seat", never a crash."""
import glob
import json
import os


def clients_dir():
    return os.environ.get("POSTCALL_CLIENTS_DIR") or os.path.expanduser("~/.orchestra/state/clients")


def registry_file():
    return os.environ.get("POSTCALL_REGISTRY_FILE") or os.path.expanduser("~/.orchestra/registry.json")


def _norm(name):
    return " ".join(name.split()).casefold() if isinstance(name, str) else ""


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _contacts(client):
    c = client.get("contacts")
    values = c.values() if isinstance(c, dict) else c if isinstance(c, list) else ()
    for contact in values:
        if isinstance(contact, dict):
            aliases = contact.get("aliases") if isinstance(contact.get("aliases"), list) else []
            yield {_norm(n) for n in [contact.get("name"), *aliases] if _norm(n)}


def is_live(seat):
    reg = _load(registry_file())
    agents = reg.get("agents") if isinstance(reg, dict) else None
    row = agents.get(seat) if isinstance(agents, dict) and seat else None
    return isinstance(row, dict) and row.get("status") == "online"


def resolve(counterpart):
    key = _norm(counterpart)
    if not key or key == "unidentified":
        return None
    found = set()
    for path in sorted(glob.glob(os.path.join(clients_dir(), "*", "client.json"))):
        client = _load(path)
        if not isinstance(client, dict) or not isinstance(client.get("pm_agent"), str) or not client["pm_agent"]:
            continue
        if any(key in names for names in _contacts(client)):
            found.add(client["pm_agent"])
    if len(found) != 1:
        return None
    seat = found.pop()
    return seat if is_live(seat) else None
