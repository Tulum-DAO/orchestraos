"""Participants: who was on a call and how far each identity is trusted (design §2a, §3 rule 3).

Three sources are combined:

  * Pocket's own speaker names (Shaw, 2026-11-27): a diarized recording whose label is a person's
    name ("Dana Reyes", not SPEAKER_02) identifies that speaker by itself. It is mapped and VERIFIED
    with no calendar at all, and neither the extractor nor the calendar can rename it;

  * the calendar: the event the recording falls in (calendar_ics.match_call) gives the INVITED SET,
    i.e. its attendees minus Shaw and anyone who declined. Invitees come with an email address,
    which is what makes an identity verifiable;
  * the transcript: for generic diarization labels (SPEAKER_00, SPEAKER_01, ...) the extractor
    proposes label -> name mappings with a quoted evidence sentence ("Hi Dana, thanks ...").

Code accepts a proposed mapping only when the evidence really is in the transcript AND the name is
someone we already know of (an invitee or a contact). The one exception is the two-party shortcut:
a call with exactly two labels whose event invited exactly one person besides Shaw needs no
evidence, because once Shaw's label is known the other voice can only be that invitee.

A name is VERIFIED when it is a Pocket-named label, or when it is mapped to a label and is an invitee of
the matched event. A Pocket name that is exactly an invitee's name gets that invitee's email. Pocket
names verify only when POSTCALL_SHAW_NAMES is set and Shaw's own label is proven (a quoted proof, or
his configured name). Everything else stays unverified, and nothing here raises on missing or
malformed input: an unknown stays unknown and the card shows it.
"""
import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo

from . import calendar_ics, deadlines, extract, sources


@dataclass
class Participant:
    name: str
    email: str | None
    label: str | None       # diarization label when the person spoke, else None
    status: str             # "spoke" (mapped to a label) or "invited" (invited, never mapped)


@dataclass
class Roster:
    event: calendar_ics.Event | None = None
    participants: list = field(default_factory=list)
    label_names: dict = field(default_factory=dict)     # label -> canonical name
    verified_names: set = field(default_factory=set)
    shaw_label: str | None = None                       # as claimed by the extractor
    shaw_voices: frozenset = frozenset()                # every label that is Shaw (extract.shaw_voices)
    labels: tuple = ()                                  # distinct transcript labels, in order


@dataclass
class _Person:
    """One identity we know of: an invitee (keyed by email) or a contact."""
    name: str
    email: str | None
    invited: bool
    keys: set = field(default_factory=set)              # normalized names/aliases it answers to


# A person's name: letters, spaces and . ' - only (no digits or underscores), at least two letters.
_NAME_SHAPE = re.compile(r"[^\W\d_][^\W\d_ .'\u2019-]*(?:[ .'\u2019-]+[^\W\d_][^\W\d_ .'\u2019-]*)*\.?")
# Words a diarizer uses for a voice it could not name; any of them (or its plural) anywhere makes the
# label generic, and the longer stems are refused even inside a run-together word ("SpeakerA").
_GENERIC_WORDS = {"speaker", "spk", "spkr", "unknown", "unidentified", "guest", "participant", "person",
                  "voice", "host", "me", "other", "you", "caller", "user", "narrator", "interviewer",
                  "interviewee", "moderator", "attendee", "multiple"}
_GENERIC_STEMS = ("speaker", "unknown", "unidentified", "participant", "attendee")


def _pocket_named(label):
    """True only for a label shaped like a person's name ("Dana Reyes", "Mary-Jane O'Neil"); False for
    SPEAKER_02, "Speaker A", "Unknown Speaker", "Person 1", "Host", "S1" and anything else generic.
    Fails closed: a doubtful label is generic and keeps the evidence/invitee rules."""
    if not isinstance(label, str):
        return False
    label = unicodedata.normalize("NFC", label.strip())
    if not _NAME_SHAPE.fullmatch(label) or any(stem in label.casefold() for stem in _GENERIC_STEMS):
        return False
    words = [w for w in re.split(r"[ .'\u2019-]+", label.casefold()) if w]
    generic = any(w in _GENERIC_WORDS or (w.endswith("s") and w[:-1] in _GENERIC_WORDS) for w in words)
    return sum(len(w) for w in words) >= 2 and not generic


def _invitee_named(people, label):
    """The invitee whose name is exactly `label` (case/space-insensitive), or None. Only an exact
    invitee name lends an email to a Pocket-named speaker; aliases and contacts never do."""
    hits = [p for p in people if p.invited and _norm(p.name) == _norm(label)]
    return hits[0] if len(hits) == 1 else None


def _norm(name):
    """Case-insensitive, whitespace-collapsed, NFC form used for every name comparison."""
    return extract.norm_name(name)


def _contact_entries(contacts):
    """Contacts as (name, email|None, [keys]), skipping anything that isn't a usable dict."""
    for c in contacts or ():
        if not isinstance(c, dict) or not _norm(c.get("name")):
            continue
        aliases = c.get("aliases") if isinstance(c.get("aliases"), (list, tuple)) else ()
        email = c.get("email").strip().lower() if isinstance(c.get("email"), str) and c.get("email").strip() else None
        yield c["name"].strip(), email, [_norm(c["name"])] + [_norm(a) for a in aliases if _norm(a)]


def _directory(invitees, contacts):
    """Everyone a name may resolve to. A contact whose email belongs to an invitee is merged into
    that invitee (so a contact alias like "Dana" can still reach the verified invitee)."""
    people, by_email = [], {}
    for a in invitees:
        person = _Person(name=a.cn or a.email, email=a.email, invited=True,
                         keys={_norm(a.cn)} if a.cn else set())
        people.append(person)
        by_email[a.email] = person
    for name, email, keys in _contact_entries(contacts):
        if email in by_email:
            by_email[email].keys.update(keys)
        else:
            people.append(_Person(name=name, email=email, invited=False, keys=set(keys)))
    return people


def _lookup(people, name):
    """The single person answering to `name` (exact, case-insensitive), or None if no one or more
    than one does: an ambiguous name is never guessed."""
    key = _norm(name)
    hits = [p for p in people if key and key in p.keys]
    return hits[0] if len(hits) == 1 else None


def _match_event(call, events):
    try:
        start = dt.datetime.fromisoformat(call.created_at.replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(sources.call_end(call))
        return calendar_ics.match_call(list(events or ()), start, end)
    except Exception:
        return None                 # no usable call time or calendar: no event, not a crash


def _labels(transcript):
    seen = []
    for label, _, _ in extract.turns(transcript or ""):
        if label not in seen:
            seen.append(label)
    return tuple(seen)


def _proposed(raw, call, labels, shaw_label, people):
    """(label, person) pairs from the extractor's speaker_names that pass both checks: the evidence
    sentence is in the transcript, and the name is an invitee or a contact."""
    entries = raw.get("speaker_names")
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict):
            continue
        label, evidence = entry.get("label"), entry.get("evidence")
        if label not in labels or label == shaw_label:
            continue                # unknown label, or an attempt to rename Shaw's own voice
        if not isinstance(evidence, str) or extract.locate_quote(evidence, call.transcript) is None:
            continue                # evidence must be verbatim in this call
        person = _lookup(people, entry.get("name"))
        if person is None:
            continue                # name not in the invited set or contacts
        yield label, person


def _shortcut(event, invitees, labels, voices, people):
    """Two-party shortcut: one invitee besides Shaw, Shaw's voice(s) known, exactly one other voice."""
    if event is None or len(invitees) != 1 or not voices:
        return None
    others = [l for l in labels if l not in voices]
    if len(others) != 1:
        return None
    other = others[0]
    person = next(p for p in people if p.invited and p.email == invitees[0].email)
    return other, person


def resolve(call, raw, events, *, shaw_emails, contacts=()):
    """Build the call's Roster (design §2a). Never raises on missing or odd input. Known limit: a
    Pocket name that is only part of an invitee's name ("Dana" vs invitee "Dana Reyes") is kept as
    its own person, so the card can list both if a generic label is also mapped to the invitee."""
    raw = raw if isinstance(raw, dict) else {}
    labels = _labels(getattr(call, "transcript", ""))
    shaw_label = raw.get("shaw_label") if isinstance(raw.get("shaw_label"), str) else None
    event = _match_event(call, events)
    try:
        invitees = calendar_ics.invited(event, set(shaw_emails or ())) if event else []
    except Exception:
        invitees = []
    people = _directory(invitees, contacts)

    # Pocket-named labels first: the name is the identity (an invitee of exactly that name lends its
    # email). A label naming Shaw is never a participant, even when the extractor claimed another one.
    shaw_names = extract._shaw_names()
    voices = frozenset(extract.shaw_voices(shaw_label, labels))
    named, lent = {}, set()                             # lent: ids of invitees a named label stands for
    for label in labels:
        if label == shaw_label or label in voices or _norm(label) in shaw_names or not _pocket_named(label):
            continue
        invitee = _invitee_named(people, label)
        named[label] = _Person(name=label.strip(), email=invitee.email if invitee else None,
                               invited=False, keys={_norm(label)})
        if invitee:
            lent.add(id(invitee))
    named_ids = {id(p) for p in named.values()} | lent
    generic = tuple(l for l in labels if l not in named and _norm(l) not in shaw_names)
    # Pocket names verify only when Shaw's names are configured (so a named label can be ruled out as
    # his own voice) AND his label is proven: a generic label (ground() requires a quoted proof whenever
    # there are items) or his configured name. Anything else fails closed: names are shown but stay
    # unverified.
    shaw_proven = bool(shaw_names) and bool(voices) and (
        not _pocket_named(shaw_label) or _norm(shaw_label) in shaw_names)

    pairs = [(l, p) for l, p in _proposed(raw, call, generic, shaw_label, people) if id(p) not in named_ids]
    shortcut = _shortcut(event, invitees, labels, voices, people) if not named else None
    if shortcut:
        pairs.append(shortcut)

    # Conflicts are dropped, not resolved: a label claimed for two people, or one person claimed
    # for two labels, means the evidence disagrees with itself.
    by_label, by_person = {}, {}
    for label, person in pairs:
        by_label.setdefault(label, set()).add(id(person))
        by_person.setdefault(id(person), set()).add(label)
    mapped = dict(named)
    for label, person in pairs:
        if len(by_label[label]) == 1 and len(by_person[id(person)]) == 1:
            mapped[label] = person

    roster = Roster(event=event, shaw_label=shaw_label, shaw_voices=voices, labels=labels)
    for label in labels:            # spoken participants, in order of first speech
        person = mapped.get(label)
        if person is None:
            continue
        roster.label_names[label] = person.name
        roster.participants.append(Participant(person.name, person.email, label, "spoke"))
        if person.invited or (label in named and shaw_proven):
            roster.verified_names.add(person.name)
    spoken_ids = {id(p) for p in mapped.values()} | lent
    for person in people:           # invited but never mapped to a voice
        if person.invited and id(person) not in spoken_ids:
            roster.participants.append(Participant(person.name, person.email, None, "invited"))
    return roster


def with_line(roster):
    """The card's `With:` entries: verified speakers ✓, other speakers (unverified), invitees who
    were never mapped (invited). Nothing known at all reads "unidentified"."""
    out = []
    for p in roster.participants:
        if p.status == "spoke":
            out.append(f"{p.name} ✓" if p.name in roster.verified_names else f"{p.name} (unverified)")
        elif p.status == "invited":
            out.append(f"{p.name} (invited)")
    return out or ["unidentified"]


def _shaw_side_verified(roster):
    """A Shaw item is verified when Shaw's voice is known, at least one other voice is verified, and
    every NAMED other voice (mapped to a person) is verified. A voice nobody could name is ignored
    (Shaw's choice on apr_e300aa64, 11-28); a voice mapped to an unverified name still holds. No
    calendar event is required."""
    if not roster.shaw_voices:
        return False                # no claim, or a label names Shaw but another was claimed
    others = [l for l in roster.labels if l not in roster.shaw_voices]
    named = [l for l in others if l in roster.label_names]
    if not (named and all(roster.label_names[l] in roster.verified_names for l in named)):
        return False
    # An unnamed voice is ignored only when Shaw is identified by his configured name; a Shaw label
    # proven by a quote alone is weaker, so there every other voice must still be verified.
    return len(named) == len(others) or extract.norm_name(roster.shaw_label) in extract._shaw_names()


def apply_trust(items, roster):
    """Copies of `items` with trust upgraded to "verified" where the roster supports it, and each
    participant item's counterpart set to the mapped name of whoever said it. Inputs are untouched."""
    shaw_ok = _shaw_side_verified(roster)
    out = []
    for item in items or ():
        fields = dict(item.fields or {})
        trust = item.trust
        if item.owner == "participant":
            name = roster.label_names.get(item.speaker)
            if name:
                fields["counterpart"] = name
                if name in roster.verified_names:
                    trust = "verified"
        elif item.owner == "shaw" and shaw_ok and item.speaker in roster.shaw_voices:
            trust = "verified"
        out.append(replace(item, trust=trust, fields=fields))
    return out


def _email_for(name, roster, contacts):
    """The counterpart's email from the invited set / roster, else from contacts; None if unknown or
    ambiguous. Design §3 rule 3 is email only: no email means no next-call deadline."""
    key = _norm(name)
    if not key:
        return None
    found = {p.email for p in roster.participants if p.email and _norm(p.name) == key}
    if not found:
        found = {email for _, email, keys in _contact_entries(contacts) if email and key in keys}
    return found.pop() if len(found) == 1 else None


def next_call_due(counterpart_name, roster, events, contacts, after_iso, tz):
    """deadlines.Due for "by the next call" with this counterpart: one hour before the next
    meeting they are an attendee of (by email), in `tz`. None when unknown; the caller keeps it
    unknown rather than guessing."""
    try:
        email = _email_for(counterpart_name, roster, contacts)
        if email is None:
            return None
        after = dt.datetime.fromisoformat(after_iso.replace("Z", "+00:00"))
        event = calendar_ics.next_with_attendee(list(events or ()), email, after)
        if event is None:
            return None
        zone = ZoneInfo(tz)
        due = (event.start - dt.timedelta(hours=1)).astimezone(zone)
        local_day = event.start.astimezone(zone).date().isoformat()
        name = counterpart_name.strip()
        for p in roster.participants:   # prefer the canonical spelling when the roster has it
            if p.email == email:
                name = p.name
                break
        source = f"next call with {name} ({event.summary}, {local_day}, uid {event.uid[:8]})"
        return deadlines.Due(due.isoformat(timespec="seconds"), source)
    except Exception:
        return None
