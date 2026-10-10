"""The brain seam (services/arturo/brain.py) is the ONLY caller of a model client.

Ported upstream code used to call a module-level `client.chat.completions.create(...)`. Public has no
such client, so the call raised NameError, an except swallowed it, and the feature silently never ran
(caught while porting EMPTY-AFTER-TOOLS: every retry fell straight to the fallback line). Any file
here other than brain.py that names `.chat.completions.create` is that bug again.
"""
import pathlib
import re

HERE = pathlib.Path(__file__).parent
CALL = re.compile(r"\.chat\.completions\.create\(")


def test_only_brain_py_calls_a_model_client():
    offenders = []
    for p in sorted(HERE.glob("*.py")):
        if p.name == "brain.py" or p.name.startswith("test_"):
            continue
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if CALL.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{p.name}:{n}: {line.strip()}")
    assert not offenders, "call the brain seam (_turn_brain().complete / brain.complete):\n" + "\n".join(offenders)
