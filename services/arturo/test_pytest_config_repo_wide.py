"""RED-first: the async test mode must hold for EVERY invocation form, not only when
pytest happens to pick services/arturo as its rootdir.

Before this test, asyncio_mode=auto lived in services/arturo/pytest.ini. pytest chooses ONE
configfile from the common ancestor of the paths on the command line, so `pytest services`
(and the whole-repo run a contributor or CI does) resolved rootdir to the repo root, found no
ini, ran in STRICT mode and failed the eight bare `async def` gateway tests with
"async def functions are not natively supported" -- green when run as file args, red inside
the package. The config now lives at the repo root, so the mode is the same for all forms.
"""
import pathlib


def test_asyncio_mode_is_auto_for_this_invocation(request):
    assert request.config.getini("asyncio_mode") == "auto"


def test_rootdir_is_the_repo_root(request):
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    assert pathlib.Path(request.config.rootpath).resolve() == repo_root
