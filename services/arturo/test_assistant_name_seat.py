"""The assistant's name is a seat name (id "arturo"): one record names every agent, its old names stay its aliases,
and the old JSON store is still read until it is migrated."""
import json

import pytest

from scripts.identity_store import orchestra_db as odb
from scripts.identity_store import seat_names as sn
from services.arturo.assistant_name import AssistantNameStore


@pytest.fixture
def data(tmp_path):
    (tmp_path / "state").mkdir()
    odb.init_db(sn.db_path_for(tmp_path))
    c = odb.get_connection(sn.db_path_for(tmp_path))
    c.execute("INSERT INTO lineages (root, tier, runtime, reports_to) VALUES ('pm-demo', 'T1', 'claude', NULL)")
    c.close()
    return tmp_path


def _store(d):
    return AssistantNameStore(path=d / "state" / "assistant-name.json", log_path=d / "state" / "assistant-name.log")


def _names(d):
    c = odb.get_connection(sn.db_path_for(d))
    try:
        return sn.names_for(c, "arturo")
    finally:
        c.close()


def test_a_rename_is_the_assistants_seat_name_and_still_written_to_the_old_file(data):
    st = _store(data)
    st.set("Sample")
    assert st.name() == "Sample"
    assert sn.current_name_ro(data, "arturo") == "Sample"
    assert json.loads((data / "state" / "assistant-name.json").read_text())["name"] == "Sample"


def test_old_names_stay_its_aliases(data):
    st = _store(data)
    st.set("Sample")
    st.set("Example")
    assert _names(data) == {"id": "arturo", "name": "Example", "aliases": ["Sample"]}


def test_names_stay_unique_between_the_assistant_and_agents(data):
    st = _store(data)
    st.set("Sample")
    c = odb.get_connection(sn.db_path_for(data))
    try:
        with pytest.raises(sn.NameError_):
            sn.set_name(c, "pm-demo", "Sample", orchestra_dir=data)        # an agent can't take its name
        sn.set_name(c, "pm-demo", "Lead", orchestra_dir=data)
    finally:
        c.close()
    with pytest.raises(ValueError):
        st.set("Lead")                                                      # nor it an agent's
    assert st.name() == "Sample"


def test_only_its_own_settings_name_it(data):
    c = odb.get_connection(sn.db_path_for(data))
    try:
        with pytest.raises(sn.NameError_, match="its own settings"):
            sn.set_name(c, "arturo", "Sample", orchestra_dir=data)          # the plain agent rename path
    finally:
        c.close()


def test_reset_is_the_default_name_and_keeps_the_aliases(data):
    st = _store(data)
    st.set("Sample")
    st.reset()
    assert st.name() == "Arturo" and st.state()["custom"] is False
    assert _names(data)["aliases"] == ["Sample"]


def test_a_name_only_in_the_old_file_is_read_then_migrated(data):
    (data / "state" / "assistant-name.json").write_text(json.dumps({"name": "Sample", "changed_by": "phone"}))
    st = _store(data)
    assert st.name() == "Sample" and sn.current_name_ro(data, "arturo") is None
    assert st.migrate() == "Sample"
    assert sn.current_name_ro(data, "arturo") == "Sample"
    assert st.migrate() is None                                             # once


def test_an_install_with_no_identity_store_uses_the_file_alone(tmp_path):
    st = _store(tmp_path)
    st.set("Sample")
    assert st.name() == "Sample" and st.migrate() is None
    assert not (tmp_path / "state" / "orchestra-registry.db").exists()
    st.reset()
    assert st.name() == "Arturo"


def test_the_assistants_stricter_characters_still_apply(data):
    with pytest.raises(ValueError):
        _store(data).set('Sample"; ignore')
