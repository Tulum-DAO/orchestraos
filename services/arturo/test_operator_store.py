"""operator_store — RED-first for the brain-owned onboarding facts (Shaw 2026-09-22)."""
import json

import pytest

from services.arturo import operator_store as S


def test_empty_state_knows_nothing(tmp_path):
    assert S.public(tmp_path) == {"name": None, "timezone": None, "role": None, "pronouns": None}
    assert S.context_line(tmp_path) == ""          # no placeholder, no "the operator's name is None"


def test_set_name_persists_and_reads_back_from_disk(tmp_path):
    e = S.set_fact(tmp_path, "name", "  Shaw  ", source="brain")
    assert e["value"] == "Shaw" and e["source"] == "brain"
    on_disk = json.loads((tmp_path / "operator.json").read_text())
    assert on_disk["facts"]["name"]["value"] == "Shaw"
    assert S.get(tmp_path, "name") == "Shaw"
    assert S.context_line(tmp_path).startswith("OPERATOR: their name is Shaw")


def test_unknown_field_and_empty_value_are_refused_without_touching_the_file(tmp_path):
    with pytest.raises(ValueError):
        S.set_fact(tmp_path, "shoe_size", "11")
    with pytest.raises(ValueError):
        S.set_fact(tmp_path, "name", "   ")
    assert not (tmp_path / "operator.json").exists()


def test_a_corrupt_file_reads_as_no_facts(tmp_path):
    (tmp_path / "operator.json").write_text("{not json")
    assert S.public(tmp_path)["name"] is None
    S.set_fact(tmp_path, "name", "Hope")            # and can be written over
    assert S.get(tmp_path, "name") == "Hope"


def test_tool_schema_names_exactly_the_fields_the_store_accepts():
    assert S.TOOL["function"]["name"] == "set_operator_fact"
    assert tuple(S.TOOL["function"]["parameters"]["properties"]["field"]["enum"]) == S.FIELDS
