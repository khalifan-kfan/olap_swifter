"""
Tests for the schema workflow: registry, matching, additive extension,
dataset attachment, schema-driven dummy data, ETL gates and checks, and the
constellation view. Runs on a temporary copy of tests/fixtures/schemas.
"""

import csv
import random
import shutil
from pathlib import Path

import duckdb
import pytest
import yaml

from olap_swifter.schema.constellation_view import build_view
from olap_swifter.schema.extender import apply_patch, attach_dataset, extend_schema
from olap_swifter.schema.matcher import match_registry
from olap_swifter.schema.materializer import WarehouseLoader
from olap_swifter.schema.profile import load_dictionary, load_eda, merge_profile_and_dictionary, profile_csv
from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.spec import SchemaSpecError, parse_spec
from olap_swifter.schema.synthetic import write_synthetic_dataset

SEED_DIR = Path(__file__).resolve().parent / "fixtures" / "schemas"
RESOURCES = Path(__file__).resolve().parents[3]
DATASETS = RESOURCES / "datasets"

HIV_PATCH = {
    "base": "clinical_visit",
    "reason": "HIV ART monitoring",
    "add_aliases": {"dim_patient.gender": ["sex"]},
    "add_tables": {
        "dim_regimen": {"kind": "dimension", "key": "regimen_code", "synthetic": {"rows": 4}, "columns": {
            "regimen_code": {"type": "VARCHAR", "role": "key", "required": True},
            "regimen_line": {"type": "VARCHAR", "role": "attribute", "allowed": ["First line", "Second line"]},
        }},
        "fact_viral_load": {"kind": "fact", "links": ["dim_regimen"], "grain": ["visit_id"], "columns": {
            "viral_load_copies": {"type": "DOUBLE", "role": "measure", "range": [0, 10000000]},
            "vl_suppressed": {"type": "VARCHAR", "role": "attribute", "allowed": ["Y", "N"]},
        }},
    },
}


@pytest.fixture
def registry(tmp_path):
    d = tmp_path / "schemas"
    shutil.copytree(SEED_DIR, d)
    return SchemaRegistry(d)


@pytest.fixture
def hiv_files(tmp_path):
    r = random.Random(3)
    sexes = {f"P{i:03d}": r.choice(["Female", "Male"]) for i in range(1, 31)}
    regimens = [("TDF/3TC/DTG", "First line"), ("AZT/3TC/LPV/r", "Second line")]
    rows = []
    for v in range(1, 81):
        pid = r.choice(list(sexes))
        reg = r.choice(regimens)
        vl = round(r.uniform(20, 100000))
        rows.append({"visit_id": f"HV{v:04d}", "patient_id": pid, "sex": sexes[pid], "facility_id": "F01",
                     "visit_date": f"2025-0{r.randint(1, 9)}-1{r.randint(0, 9)}", "regimen_code": reg[0],
                     "regimen_line": reg[1], "viral_load_copies": vl, "vl_suppressed": "Y" if vl < 1000 else "N"})
    data = tmp_path / "hiv.csv"
    with open(data, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cat = tmp_path / "hiv_catalogue.csv"
    cat.write_text("variable,label,type,values\n" + "\n".join(
        f"{k},{k},string," for k in rows[0]) + "\n")
    eda = tmp_path / "hiv_eda.yaml"
    eda.write_text(yaml.safe_dump(profile_csv(str(data), max_categories=20)))
    return {"data": str(data), "catalogue": str(cat), "eda": str(eda)}


def _variables(files):
    return merge_profile_and_dictionary(load_eda(files["eda"]), load_dictionary(files["catalogue"]))


def test_seed_schemas_are_valid_and_reuse_tables(registry):
    assert set(registry.ids()) == {"amr_surveillance", "clinical_visit"}
    for spec in registry.all():
        assert registry.table_conflicts(spec) == []
    owners = registry.table_owners()
    assert sorted(owners["dim_visit"]) == ["amr_surveillance", "clinical_visit"]
    assert owners["fact_ast_results"] == ["amr_surveillance"]


@pytest.mark.parametrize("schema_id", ["clinical_visit", "amr_surveillance"])
def test_dummy_data_passes_every_check(registry, tmp_path, schema_id):
    spec = registry.get(schema_id)
    out = write_synthetic_dataset(spec, str(tmp_path / "dummy"), n_visits=80)
    for key in ("data", "catalogue", "eda"):
        assert Path(out[key]).exists()
    report = WarehouseLoader(duckdb.connect()).load(spec, out["data"], f"{schema_id}_dummy", is_synthetic=True)
    assert report["loaded"], report["preflight"]["failures"]
    assert report["unmapped_variables"] == []
    assert report["postload"]["all_passed"], report["postload"]["failures"]


def test_schemas_share_one_constellation(registry, tmp_path):
    conn = duckdb.connect()
    loader = WarehouseLoader(conn)
    for sid in ("clinical_visit", "amr_surveillance"):
        out = write_synthetic_dataset(registry.get(sid), str(tmp_path / sid), n_visits=50)
        report = loader.load(registry.get(sid), out["data"], f"{sid}_dummy", is_synthetic=True)
        assert report["loaded"]
    # The second schema only created its own tables; shared ones were reused.
    assert set(report["ddl_changes"]) == {"dim_organism", "dim_antibiotic", "fact_ast_results"}
    sources = conn.execute("SELECT source_id, schema_id FROM dim_data_source ORDER BY 1").fetchall()
    assert sources == [("amr_surveillance_dummy", "amr_surveillance"), ("clinical_visit_dummy", "clinical_visit")]


def test_hiv_dataset_extends_then_fits(registry, hiv_files, tmp_path):
    variables = _variables(hiv_files)
    before = match_registry(variables, registry)
    assert before["recommendation"] == "extend"  # shares spine/patient/facility, < 80% different
    assert before["tied_with"] == ["clinical_visit"] or before["best_schema"] == "clinical_visit"

    patch = tmp_path / "patch.yaml"
    patch.write_text(yaml.safe_dump(HIV_PATCH))
    spec = extend_schema(str(patch), registry)
    tagged = attach_dataset(spec, "hiv_site_b", variables, hiv_files["catalogue"], hiv_files["eda"],
                            main_fact="fact_viral_load")
    registry.save(spec)
    assert "fact_viral_load.viral_load_copies" in tagged["columns"]
    assert list(registry.dir.glob("clinical_visit*.yaml")) == [registry.dir / "clinical_visit.yaml"]  # no versions

    after = match_registry(variables, registry)
    assert after["recommendation"] == "fit" and after["best_schema"] == "clinical_visit"

    conn = duckdb.connect()
    report = WarehouseLoader(conn).load(registry.get("clinical_visit"), hiv_files["data"], "hiv_site_b")
    assert report["loaded"] and report["postload"]["all_passed"], report["postload"]["failures"]
    coverage = [r for r in report["postload"]["results"] if r["check"] == "spine_coverage"][0]
    assert coverage["target"].startswith("fact_viral_load") and coverage["actual"] == 100.0

    view = build_view(registry, highlight="hiv_site_b")
    assert "fact_viral_load" in view["highlighted_tables"]
    assert "fact_ast_results" not in view["highlighted_tables"]
    assert "fact_encounters" not in view["highlighted_tables"]  # carries visit_id, but no HIV columns


def test_patches_are_additive_only(registry):
    spec = registry.get("clinical_visit")
    with pytest.raises(SchemaSpecError, match="already exists"):
        apply_patch(spec, {"reason": "x", "add_columns": {"dim_patient": {"gender": {"type": "BIGINT"}}}})
    with pytest.raises(SchemaSpecError, match="additive only"):
        apply_patch(spec, {"reason": "x", "rename_columns": {"dim_patient.gender": "sex"}})
    with pytest.raises(SchemaSpecError, match="reason"):
        apply_patch(spec, {"add_aliases": {"dim_patient.gender": ["sex"]}})


def test_new_schema_reusing_a_table_must_agree(registry, tmp_path):
    clash = registry.get("clinical_visit").to_yaml_dict()
    clash["schema_id"] = "clash"
    clash["tables"]["dim_patient"]["columns"]["gender"]["type"] = "BIGINT"
    clash["tables"]["dim_patient"]["columns"]["gender"].pop("allowed")
    path = tmp_path / "clash.yaml"
    path.write_text(yaml.safe_dump(clash))
    with pytest.raises(SchemaSpecError, match="dim_patient.gender"):
        registry.register(path)


def test_spec_validation_catches_structural_errors():
    base = {"schema_id": "s", "spine": {"table": "dim_visit", "key": "visit_id"}, "main_fact": "f",
            "tables": {"dim_visit": {"kind": "dimension", "key": "visit_id",
                                     "columns": {"visit_id": {"type": "VARCHAR", "role": "key"}}},
                       "f": {"kind": "fact", "columns": {"x": {"type": "VARCHAR", "role": "measure"}}}}}
    with pytest.raises(SchemaSpecError, match="measures must be numeric"):
        parse_spec(base)


def test_gate_blocks_conflicting_entity_attributes(registry):
    """The AMR sample assigns patients different genders across rows; it must not load silently."""
    report = WarehouseLoader(duckdb.connect()).load(
        registry.get("amr_surveillance"), str(DATASETS / "sample_amr_clinical.csv"), "sample_amr")
    assert report["loaded"] is False
    failed = {(f["check"], f["target"]) for f in report["preflight"]["failures"]}
    assert ("entity_consistency", "dim_patient") in failed


def test_eda_suppresses_small_categories(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("id,ward\n" + "\n".join(f"{i},{'A' if i < 9 else 'Rare'}" for i in range(10)) + "\n")
    eda = profile_csv(str(path), max_categories=20, min_category_count=5)
    ward = [v for v in eda["variables"] if v["name"] == "ward"][0]["stats"]
    assert ward["categories"] == {"A": 9}
    assert ward["suppressed_categories"] == {"labels": 1, "rows": 1}


def test_dummy_data_is_capped_at_whole_visits(registry, tmp_path):
    spec = registry.get("clinical_visit")
    out = write_synthetic_dataset(spec, str(tmp_path / "dummy"), n_visits=5000)
    assert out["capped"] and out["rows"] <= 400 == out["max_rows"]
    with open(out["data"]) as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == out["rows"] and len({r["visit_id"] for r in rows}) == out["visits"]
    report = WarehouseLoader(duckdb.connect()).load(spec, out["data"], "capped_dummy", is_synthetic=True)
    assert report["loaded"] and report["postload"]["all_passed"], report["postload"]["failures"]


def test_describe_from_inputs_folder(registry, hiv_files, tmp_path):
    from olap_swifter import describe
    from olap_swifter.schema.describe import blank_description
    from olap_swifter.schema.profile import load_description

    template = yaml.safe_load((RESOURCES / "templates" / "dataset_description.yaml").read_text())
    shape = lambda d: {k: sorted(v) if isinstance(v, dict) else None for k, v in d.items()}  # noqa: E731
    assert shape(template) == shape(blank_description())

    # One dataset delivered as two exports with two dictionaries.
    inputs = tmp_path / "inputs"
    folder = inputs / "hiv_site_b"
    for d in ("catalogue", "data", "codes"):
        (folder / d).mkdir(parents=True)
    with open(hiv_files["data"]) as fh:
        rows = list(csv.DictReader(fh))
    visit_cols = ["visit_id", "patient_id", "sex", "facility_id", "visit_date"]
    vl_cols = ["visit_id", "regimen_code", "regimen_line", "viral_load_copies", "vl_suppressed"]
    for name, cols in (("visits", visit_cols), ("viral_loads", vl_cols)):
        with open(folder / "data" / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        (folder / "catalogue" / f"{name}.csv").write_text(
            "variable,label,type,values\n" + "".join(f"{c},{c},string,\n" for c in cols))
    (folder / "codes" / "regimens.csv").write_text("code,label\nTDF/3TC/DTG,a\nAZT/3TC/LPV/r,b\n")
    (folder / "codes" / "who_stage.csv").write_text("code\n1\n2\n3\n4\n")

    out = describe("hiv_site_b", "Adults on ART.", "Share virally suppressed?", "one viral load test",
                   "viral load test", codes={"regimen_code": "regimens.csv"}, inputs_dir=str(inputs),
                   registry=registry, quiet=True, direct_identifiers=["patient_name"])
    assert out["catalogue_files"] == ["viral_loads.csv", "visits.csv"]
    assert out["eda_generated"] == 2 and out["missing_required"] == []
    assert [f["rows"] for f in out["data_files"]] == [80, 80]
    assert out["sources"] == ["hiv_site_b_viral_loads", "hiv_site_b_visits"]
    assert out["code_lists"] == [{"variable": "regimen_code", "file": "codes/regimens.csv", "entries": 2},
                                 {"variable": "who_stage", "file": "codes/who_stage.csv", "entries": 4}]
    assert out["skill_to_fill"] == ["visit.identifier", "relationship_to_existing.schema",
                                    "relationship_to_existing.decision"]

    # Folders of catalogues and EDAs merge into one variable list; the shared key appears once.
    variables = merge_profile_and_dictionary(load_eda(out["eda"]), load_dictionary(out["catalogue"]))
    assert sorted(v["name"] for v in variables) == sorted(set(visit_cols + vl_cols))
    assert [v for v in variables if v["name"] == "visit_id"][0]["files"] == ["viral_loads", "visits"]

    # The skill fills its sections; re-running describe keeps them and makes no new EDA.
    desc_path = folder / "description.yaml"
    doc = yaml.safe_load(desc_path.read_text())
    doc["visit"]["identifier"] = "visit_id"
    doc["relationship_to_existing"].update({"schema": "clinical_visit", "decision": "extend"})
    desc_path.write_text(yaml.safe_dump(doc))
    again = describe("hiv_site_b", inputs_dir=str(inputs), quiet=True)
    assert again["skill_to_fill"] == [] and again["eda_generated"] == 0 and len(again["code_lists"]) == 2
    assert load_description(str(desc_path))["description"]["dataset"]["data_files"] == \
        ["data/viral_loads.csv", "data/visits.csv"]

    # Each data file loads as its own source of the one dataset.
    patch = tmp_path / "patch.yaml"
    patch.write_text(yaml.safe_dump(HIV_PATCH))
    spec = extend_schema(str(patch), registry)
    attach_dataset(spec, "hiv_site_b", variables, out["catalogue"], out["eda"], main_fact="fact_viral_load")
    registry.save(spec)
    loader = WarehouseLoader(duckdb.connect())
    for source, f in zip(out["sources"], out["data_files"]):
        report = loader.load(registry.get("clinical_visit"), str(folder / f["data"]), source)
        assert report["loaded"] and report["postload"]["all_passed"], (source, report["postload"]["failures"])
    assert registry.get("clinical_visit").dataset_for_source("hiv_site_b_viral_loads")["main_fact"] == \
        "fact_viral_load"

    with pytest.raises(TypeError, match="unknown description field"):
        describe("hiv_site_b", inputs_dir=str(inputs), typo_field=1, quiet=True)
    with pytest.raises(ValueError, match="must be inside"):
        describe("hiv_site_b", codes={"x": "../../../../etc/passwd"}, inputs_dir=str(inputs), quiet=True)
    with pytest.raises(FileNotFoundError, match="inputs folder"):
        describe("nope", inputs_dir=str(inputs), quiet=True)


def test_minimal_csv_eda_round_trips(hiv_files, tmp_path):
    from olap_swifter.schema.profile import write_eda
    eda = profile_csv(hiv_files["data"], max_categories=20)
    path = write_eda(eda, str(tmp_path / "eda" / "hiv.csv"))
    back = load_eda(path)
    assert back["row_count"] == 80 and back["column_count"] == eda["column_count"]
    vl = {v["name"]: v for v in back["variables"]}
    assert vl["viral_load_copies"]["type"] == "BIGINT" and vl["viral_load_copies"]["stats"]["min"] >= 20
    assert set(vl["vl_suppressed"]["stats"]["categories"]) <= {"Y", "N"}
    assert load_eda(str(tmp_path / "eda"))["variables"]  # a folder of CSV EDAs reads too


def test_ward_crosswalk_keeps_raw_label_and_maps_standard_type(registry, tmp_path):
    from olap_swifter.schema.vocabulary import VocabularyError, crosswalk_path, label_counts, merge_crosswalk

    data = tmp_path / "wards.csv"
    wards = [("F01", "Pediatric Ward"), ("F01", "  paediatric   WARD "), ("F02", "Ward 4B"), ("F01", "Ward 4B"),
             ("F02", "Burns Unit"), ("F03", "ICU")]
    data.write_text("visit_id,patient_id,facility_id,visit_date,ward\n" + "".join(
        f"V{i:03d},P{i:03d},{f},2025-02-0{1 + i % 9},{w}\n" for i, (f, w) in enumerate(wards * 3)))

    # Ward 4B is surgical at F02 but medical at F01: a scoped entry per site.
    xw = crosswalk_path(registry.dir, "ward_type")
    doc = yaml.safe_load(xw.read_text())
    doc["entries"] += [{"value": "Ward 4B", "scope": "F02", "maps_to": "Surgical"},
                       {"value": "Ward 4B", "scope": "F01", "maps_to": "Medical"}]
    xw.write_text(yaml.safe_dump(doc))

    report = WarehouseLoader(duckdb.connect()).load(registry.get("clinical_visit"), str(data), "wards")
    assert report["loaded"] is False
    gate = [r for r in report["preflight"]["results"] if r["check"] == "crosswalk_coverage"][0]
    assert gate["actual"] == 3 and gate["unmapped"] == [{"value": "Burns Unit", "scope": "F02", "rows": 3}]

    # The skill lists the vocabulary, merges new labels into the crosswalk and maps them.
    labels = label_counts([str(data)], "ward", by="facility_id")
    assert {l["value"]: l["seen_at"] for l in labels["labels"]}["Ward 4B"] == ["F01", "F02"]
    merged = merge_crosswalk(xw, "ward_type", "ward", labels["labels"])
    assert merged["unmapped"] >= 1
    doc = yaml.safe_load(xw.read_text())
    for e in doc["entries"]:
        if not e.get("maps_to"):
            e["maps_to"] = "Surgical" if e["value"] == "Burns Unit" else "Other"
    xw.write_text(yaml.safe_dump(doc))

    conn = duckdb.connect()
    report = WarehouseLoader(conn).load(registry.get("clinical_visit"), str(data), "wards")
    assert report["loaded"] and report["postload"]["all_passed"], report["postload"]["failures"]
    got = dict(conn.execute("""SELECT f.facility_id || '|' || TRIM(v.admission_ward), v.ward_type
        FROM dim_visit v JOIN dim_facility f USING (facility_id)""").fetchall())
    assert got["F01|Pediatric Ward"] == got["F01|paediatric   WARD"] == "Paediatric"  # raw label kept as recorded
    assert got["F02|Ward 4B"] == "Surgical" and got["F01|Ward 4B"] == "Medical"
    assert got["F02|Burns Unit"] == "Surgical" and got["F03|ICU"] == "ICU"

    ids = tmp_path / "ids.csv"
    ids.write_text("visit_id,patient_name\n" + "".join(f"V{i},Name {i}\n" for i in range(60)))
    with pytest.raises(VocabularyError, match="identifier or free text"):
        label_counts([str(ids)], "visit_id")
    desc = tmp_path / "description.yaml"
    desc.write_text(yaml.safe_dump({"privacy": {"direct_identifiers": ["patient_name"]}}))
    with pytest.raises(VocabularyError, match="marked as an identifier"):
        label_counts([str(ids)], "patient_name", description=str(desc))
    doc["entries"].append({"value": "Theatre", "maps_to": "Operating"})
    xw.write_text(yaml.safe_dump(doc))
    with pytest.raises(SchemaSpecError, match="not an allowed value"):
        registry.get("clinical_visit")
