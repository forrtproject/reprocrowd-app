import pytest
from decimal import Decimal

from reprocrowd.numbers import parse_printed, round_to
from reprocrowd.compare import compare_target
from reprocrowd.schemas.curation import Target
from reprocrowd.schemas.exports import OutputResult
from reprocrowd.schemas.comparison import CompareSettings


@pytest.mark.parametrize("token,value,exponent", [(".17", ".17", -2), ("0.1700", ".17", -4),
    ("1.20e-5", ".000012", -7), ("1.2e3", "1200", 2), ("−.30", "-.30", -2), ("1,234", "1234", 0)])
def test_printed(token, value, exponent):
    parsed = parse_printed(token)
    assert parsed.value == Decimal(value) and parsed.quantum == exponent


def row(expected, value, **kwargs):
    t = Target(id="T001", type="number", description="value", expected=expected,
               unreferenced=True, note="test", **kwargs)
    return compare_target(t, OutputResult(id=t.id, value=value), CompareSettings())


def test_precision_and_ties():
    assert row("1.2e-5", 9e-5).status == "deviation"
    assert row("1.2e-5", 1.24e-5).status == "match"
    assert row(".170", .1749).status == "within"
    assert row(".170", .1704).status == "match"
    assert row("~10.0", 10.4).status == "within"
    assert row("<.001", .001001).status == "deviation"
    # A tie matches either neighbour: authors' software rounds half-up or half-even.
    for printed, value in ((".12", .125), (".13", .125), ("2.67", 2.675), ("2.68", 2.675), ("-2.67", -2.675), ("-2.68", -2.675)):
        assert row(printed, value).status == "match", (printed, value)
    assert row("2.68", 2.6749).status == "within"  # not a tie
    assert "2.67 or 2.68 (tie)" in row("2.68", 2.675).reproduced_text
    assert row("<=.05", .055).status == "match" and row("<=.05", .0551).status != "match"
    assert row(">=.06", .055).status == "match" and row(">=.06", .0549).status != "match"
    assert row(.17, .1704, decimals=2).status == "match"
    assert row(.17, .1704).precision == "inferred"
    assert round_to(Decimal("1200"), 2) == Decimal("1.2e3")


def test_table_operators_and_scientific():
    t = Target(id="T001", type="table", description="table", expected=[["≤ .05", "1.2e-5"]], unreferenced=True, note="test")
    r = compare_target(t, OutputResult(id=t.id, table=[[.03, 9e-5]]), CompareSettings())
    assert [c.status for c in r.cells[0]] == ["match", "deviation"]
    assert r.cells[0][0].comparator == "<=" and r.cells[0][1].quantum == -6


def test_api_normalization_and_contradictions(client, pkg):
    url = f"/v1/packages/{pkg}/targets"
    base = dict(type="number", description="value", unreferenced=True, note="test")
    r = client.post(url, json=base | {"expected": "0.1700"})
    assert r.status_code == 201, r.text
    assert (r.json()["expected"], r.json()["printed"], r.json()["decimals"]) == (.17, "0.1700", 4)
    for bad in ({"expected": .17, "printed": ".18"}, {"expected": "<.001", "comparator": ">"},
                {"expected": ".170", "decimals": 2}):
        r = client.post(url, json=base | bad)
        assert r.status_code == 422 and r.json()["code"] == "target_contradiction", r.text
    assert client.post(url, json=base | {"expected": "1,5"}).status_code == 422
    for bad in ("0,512", "12,34", "1,2345"):  # decimal commas or malformed grouping never become integers
        assert client.post(url, json=base | {"expected": bad}).status_code == 422, bad
    r = client.post(url, json=base | {"expected": "12,345.60"})
    assert (r.json()["expected"], r.json()["printed"]) == (12345.6, "12345.60")
    r = client.patch(url + "/T001", json={"expected": ".170"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["printed"] == ".170" and r.json()["item"]["decimals"] == 3


def test_edited_operator_replaces_stored_comparator(client, pkg):
    url = f"/v1/packages/{pkg}/targets"
    base = dict(type="number", description="bound", unreferenced=True, note="test")
    assert client.post(url, json=base | {"expected": "> .10"}).status_code == 201
    r = client.patch(url + "/T001", json={"expected": "< .05"})
    assert r.status_code == 200, r.text
    assert (r.json()["item"]["comparator"], r.json()["item"]["printed"]) == ("<", ".05")
    r = client.patch(url + "/T001", json={"expected": ".04"})  # no operator typed: the stored one stays
    assert (r.json()["item"]["comparator"], r.json()["item"]["printed"]) == ("<", ".04")
    r = client.patch(url + "/T001", json={"expected": ">= .05", "printed": ".05"})  # a typed operator is never dropped
    assert r.status_code == 200 and r.json()["item"]["comparator"] == ">=", r.text
    r = client.patch(url + "/T001", json={"expected": "< .05", "comparator": ">"})
    assert r.status_code == 422 and r.json()["code"] == "target_contradiction"
    # Only number targets take a comparator from the typed value: a categorical value that looks like a bound stays text.
    cat = dict(type="categorical", description="verdict", unreferenced=True, note="test", expected="significant")
    tid = client.post(url, json=cat).json()["id"]
    r = client.patch(f"{url}/{tid}", json={"expected": "< .05"})
    assert r.status_code == 200, r.text
    assert (r.json()["item"]["expected"], r.json()["item"]["comparator"]) == ("< .05", None)
    # Changing the type to number in the same edit does take it.
    r = client.patch(f"{url}/{tid}", json={"type": "number", "expected": "> .10"})
    assert r.status_code == 200 and r.json()["item"]["comparator"] == ">", r.text


def test_long_token_survives_model_roundtrip():
    target = Target(id="T001", type="number", description="long decimal", expected="0.12345678901234567890", unreferenced=True, note="test")
    assert Target.model_validate(target.model_dump()).printed == "0.12345678901234567890"
    with pytest.raises(ValueError):
        Target(id="T002", type="number", description="contradiction", expected="<.001", comparator="=", unreferenced=True, note="test")


def test_table_inclusive_bounds_round_at_printed_precision():
    target = Target(id="T001", type="table", description="bound", expected=[["≤ .05", "1.2e-5"]], unreferenced=True, note="test")
    good = compare_target(target, OutputResult(id="T001", table=[[.054, 1.24e-5]]), CompareSettings())
    assert [cell.status for cell in good.cells[0]] == ["match", "match"]
    bad = compare_target(target, OutputResult(id="T001", table=[[.056, 9e-5]]), CompareSettings())
    assert all(cell.status != "match" for cell in bad.cells[0])


def test_llm_precision_edit_preserves_base(client, pkg):
    from test_api import setup_paper
    setup_paper(client, pkg)
    url = f"/v1/packages/{pkg}/targets"
    run = client.post(url + "/runs", json={"extraction": "ex-paper-tei"}).json()
    item = run["entries"][0]["base"]
    response = client.patch(url + "/" + item["id"], json={"expected": "0.170"})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["printed"] == "0.170" and response.json()["item"]["decimals"] == 3
    raw = client.get(url, params={"view": "raw"}).json()
    assert raw["llm_runs"][0]["entries"][0]["base"] == item
