"""The results workspace: where it lives, its README (the results key), the zip, and ydata-profiling EDAs."""

import json
import zipfile

import yaml

from olap_swifter.feedback import token_meter
from olap_swifter.feedback.worklog import log_step
from olap_swifter.paths import make_workspace, route, workspace_root, write_workspace_readme, zip_workspace
from olap_swifter.schema.profile import load_eda


def test_workspace_is_made_in_the_users_folder_outside_the_skill(tmp_path, monkeypatch):
    monkeypatch.delenv("OLAP_SWIFTER_ROOT", raising=False)
    monkeypatch.delenv("OLAP_SWIFTER_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert workspace_root() == tmp_path / "results_workspace"
    assert route("warehouse") == tmp_path / "results_workspace" / "warehouse" / "warehouse.duckdb"
    assert route("config").name == "config.yaml" and tmp_path not in route("config").parents

    monkeypatch.setenv("OLAP_SWIFTER_WORKSPACE", str(tmp_path / "elsewhere"))
    assert route("inputs") == tmp_path / "elsewhere" / "inputs"


def test_readme_is_the_results_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OLAP_SWIFTER_WORKSPACE", str(tmp_path / "ws"))
    out = make_workspace("hiv_site_b")
    for d in ("catalogue", "data", "eda", "codes", "documents"):
        assert (tmp_path / "ws" / "inputs" / "hiv_site_b" / d).is_dir()
    concept = {"study": {"main_question": "What share are suppressed?"},
               "unit_of_analysis": {"one_row_is": "one viral load test", "main_event": "viral load test"},
               "visit": {"identifier": "visit_id", "definition": "one clinic visit"},
               "relationship_to_existing": {"schema": "clinical_visit", "decision": "extend"}}
    (tmp_path / "ws" / "inputs" / "hiv_site_b" / "dataset_concept.yaml").write_text(yaml.safe_dump(concept))
    (tmp_path / "ws" / "review").mkdir(exist_ok=True)
    route("model_reviewer").write_text("<html></html>")

    readme = (tmp_path / "ws" / "README.md")
    make_workspace()
    text = readme.read_text()
    assert out["readme"] == str(readme)
    assert "[review/model_reviewer.html](review/model_reviewer.html)" in text
    assert "| hiv_site_b | clinical_visit | extend |" in text
    assert "one clinic visit (identifier: `visit_id`)" in text
    assert "## Standing decisions" in text and "## Folder structure" in text
    assert "Exploratory Data Analysis" in text


def test_zip_leaves_raw_exports_out(tmp_path, monkeypatch):
    monkeypatch.setenv("OLAP_SWIFTER_WORKSPACE", str(tmp_path / "ws"))
    make_workspace("d1")
    (tmp_path / "ws" / "inputs" / "d1" / "data" / "raw.csv").write_text("a\n1\n")
    (tmp_path / "ws" / "inputs" / "d1" / "eda" / "raw.yaml").write_text("variables: []\n")
    out = zip_workspace()
    names = zipfile.ZipFile(out["zip"]).namelist()
    assert out["raw_data_files_left_out"] == 1
    assert "ws/inputs/d1/eda/raw.yaml" in names and "ws/README.md" in names
    assert not any(n.endswith("raw.csv") for n in names)
    assert len(zipfile.ZipFile(zip_workspace(include_data=True)["zip"]).namelist()) == len(names) + 1


def test_ydata_profiling_report_is_read_as_aggregates_only(tmp_path):
    counts = {f"P{i}": 1 for i in range(30)}
    report = {"table": {"n": 30}, "sample": [], "variables": {
        "patient_id": {"type": "Text", "n_missing": 0, "p_missing": 0.0, "n_distinct": 30, "is_unique": True,
                       "value_counts_without_nan": counts},
        "age": {"type": "Numeric", "n_missing": 3, "p_missing": 0.1, "n_distinct": 20, "min": 18, "max": 70,
                "mean": 41.123456},
        "ward": {"type": "Categorical", "n_missing": 0, "p_missing": 0.0, "n_distinct": 3,
                 "value_counts_without_nan": {"A": 20, "B": 8, "Rare": 2}}}}
    path = tmp_path / "visits.json"
    path.write_text(json.dumps(report))
    eda = load_eda(str(path))
    by = {v["name"]: v for v in eda["variables"]}
    assert eda["row_count"] == 30 and eda["made_by"] == "ydata-profiling"
    assert by["patient_id"]["stats"]["unique_per_row"] and "categories" not in by["patient_id"]["stats"]
    assert by["age"]["type"] == "BIGINT" and by["age"]["stats"]["null_pct"] == 10.0
    assert by["ward"]["stats"]["categories"] == {"A": 20, "B": 8}
    assert by["ward"]["stats"]["suppressed_categories"] == {"labels": 1, "rows": 2}
    assert "P1" not in json.dumps(eda)


def test_readme_shows_the_token_weight(tmp_path, monkeypatch, isolated_root, capsys):
    monkeypatch.setenv("OLAP_SWIFTER_WORKSPACE", str(tmp_path / "ws"))
    (isolated_root / "SKILL.md").write_text("y" * 4000)
    monkeypatch.setattr(token_meter.sys, "stdout", token_meter.sys.stdout)
    hooks = []
    monkeypatch.setattr(token_meter.atexit, "register", hooks.append)
    token_meter.start(["olap-swifter", "source", "match", "--catalogue", "c", "--json"])
    print("x" * 400)
    hooks[0]()
    assert token_meter.command_name(["olap-swifter", "etl", "clinical_visit", "f.csv"]) == "etl"

    log_step("report", True, "hiv_site_b", tokens=123456)
    text = write_workspace_readme().read_text()
    assert "## Token weight" in text and "| `SKILL.md` | 1,000 |" in text
    assert "| `source match` | 1 | 101 |" in text  # 401 characters with the newline
    assert "hiv_site_b: 123,456 tokens" in text
