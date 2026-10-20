"""Tests for the vocabulary correction layer (corrections.py)."""
import json
import os

from corrections import Corrections


def test_literal_replacements():
    c = Corrections(literal=[
        {"wrong": "high trust", "right": "HITRUST"},
        {"wrong": "a car", "right": "ACR"},
        {"wrong": "m caps", "right": "MCAPS"},
    ])
    assert c.apply("we need high trust compliance") == "we need HITRUST compliance"
    assert c.apply("push it to a car pipeline") == "push it to ACR pipeline"
    assert c.apply("the m caps org") == "the MCAPS org"


def test_literal_case_insensitive_match_preserves_replacement_casing():
    c = Corrections(literal=[{"wrong": "high trust", "right": "HITRUST"}])
    assert c.apply("High Trust certified") == "HITRUST certified"


def test_literal_whole_word_only():
    # "a car" should NOT fire inside "a cartel"
    c = Corrections(literal=[{"wrong": "a car", "right": "ACR"}])
    assert c.apply("a cartel of vendors") == "a cartel of vendors"


def test_guarded_fires_only_with_context():
    c = Corrections(guarded=[
        {"wrong": "fire", "right": "FHIR", "near": ["epic", "hl7", "interop"]},
    ])
    # context present → correct
    assert c.apply("Epic exposes a fire endpoint") == "Epic exposes a FHIR endpoint"
    assert c.apply("the hl7 fire bundle") == "the hl7 FHIR bundle"
    # no context → left alone (real word)
    assert c.apply("we went rapid fire through the deck") == "we went rapid fire through the deck"


def test_guarded_gate_uses_original_line():
    # A literal replacement must not create context that trips a guarded rule.
    c = Corrections(
        literal=[{"wrong": "a car", "right": "epic"}],  # contrived
        guarded=[{"wrong": "fire", "right": "FHIR", "near": ["epic"]}],
    )
    # "epic" only appears AFTER the literal sub; gate is computed on original,
    # so "fire" must stay untouched.
    assert c.apply("a car and some fire") == "epic and some fire"


def test_guarded_avoid_protects_idiom():
    # "fire" near a health word would normally correct, but an `avoid` phrase
    # (idiom) on the same line vetoes the whole rule.
    c = Corrections(guarded=[
        {"wrong": "fire", "right": "FHIR", "near": ["api", "data"],
         "avoid": ["rapid fire"]},
    ])
    # idiom present → left alone even though "api" is on the line
    assert c.apply("rapid fire through the api list") == "rapid fire through the api list"
    # no idiom, context present → corrected
    assert c.apply("the api exposes a fire resource") == "the api exposes a FHIR resource"


def test_real_file_leaves_rapid_fire_alone():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corrections.json")
    c = Corrections.load(path)
    # The exact false-positive found in the 227-transcript dry run.
    line = "Sorry, I know that's rapid fire. If you want the FHIR endpoint"
    assert "rapid fire" in c.apply(line)
    assert "rapid FHIR" not in c.apply(line)


def test_inert_when_empty():
    c = Corrections()
    assert not c.active
    assert c.apply("nothing changes here") == "nothing changes here"


def test_load_missing_file_fails_open(tmp_path):
    c = Corrections.load(str(tmp_path / "does-not-exist.json"))
    assert not c.active
    assert c.apply("untouched") == "untouched"


def test_load_bad_json_fails_open(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    c = Corrections.load(str(p))
    assert not c.active


def test_env_disables(monkeypatch, tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"literal": [{"wrong": "a car", "right": "ACR"}]}))
    monkeypatch.setenv("RTT_CORRECTIONS_FILE", "none")
    c = Corrections.load(str(p))
    assert not c.active


def test_load_real_corrections_file():
    # The shipped corrections.json must parse and be active.
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "corrections.json")
    c = Corrections.load(path)
    assert c.active
    assert c.apply("achieve high trust") == "achieve HITRUST"
