# How to Run — `olap_swifter`

This is the practical guide. Every command, file path, SQL template and code
example lives here.

If you want to understand *why* the model is built the way it is, read
[SKILL.md](../../SKILL.md) instead. If you want the test definitions, read
[TEST.md](../../TEST.md). Terms such as crosswalk, grain, gate, patch and
alias are defined in [TERMS.md](TERMS.md).

---

## Contents
1. [Set up the environment](#1-set-up-the-environment)
2. [Where the files live](#2-where-the-files-live)
3. [Run it as an agent (the full skill)](#3-run-it-as-an-agent-the-full-skill)
4. [File formats](#4-file-formats)
5. [Run it from Python](#5-run-it-from-python)
6. [Command reference](#6-command-reference)
7. [Test the code tools](#7-test-the-code-tools)
8. [Run the full test suite](#8-run-the-full-test-suite)
9. [The datasets](#9-the-datasets)

---

## 1. Set up the environment

Everything is installed in the project virtual environment (`.venv`).

```bash
cd /Users/muwongekhalifan/Desktop/dexta_s_lab/olap_swifter
```

Check it works:

```bash
.venv/bin/python -c "import olap_swifter; print('olap_swifter is ready')"
```

If the environment does not exist yet, create it:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e resources/code_tools
```

---

## 2. Where the files live

Every location below is also in [`resources/ROUTES.yaml`](../ROUTES.yaml) by
name; the code tools resolve their paths from it, so a move is one edit there.

| Path | What it holds |
| :--- | :--- |
| `results_workspace/schemas/` | **The schema registry**, built from your datasets. One YAML file per schema (`<schema_id>.yaml`), edited in place, never versioned; `crosswalks/` beside them. Starts empty on a fresh clone. Git-ignored. |
| `resources/config.yaml` | Match threshold (`new_schema_min_difference`, default 0.8) and EDA small-cell settings. |
| `resources/templates/dataset_concept.yaml` | The dataset concept template (what the dataset is): user sections, then the sections the skill fills. |
| `results_workspace/inputs/<dataset>/` | **One folder per dataset:** `catalogue/`, `data/`, `eda/`, `codes/` (each may hold several files), `documents/` (source documents such as a protocol or SOP; the skill reads them only for the template's answers) and `dataset_concept.yaml`. Git-ignored. |
| `resources/datasets/` | Sample CSVs used by the tests (opened only by the code tools). Git-ignored. |
| `resources/code_tools/olap_swifter/schema/` | Registry, matcher, extender, loader (ETL), dummy data, checks, constellation view. |
| `resources/code_tools/olap_swifter/` | The rest of the warehouse package (cube, profiler, reviewer, process log, CLI) and `tests/`. |
| `resources/code_tools/ocr_tools/` | OCR, kept separate from the warehouse (see [OCR.md](OCR.md)). |
| `resources/code_tools/privacy_tools/` | Presidio de-identification, used by OCR. |
| `results_workspace/review/model_reviewer.html` | The review page. Rewritten after every tool step. Git-ignored. |
| `results_workspace/` | **The results workspace: everything a run writes.** Outside the skill (see below). `README.md` is its key: the review page link, the folder structure, the decisions made per dataset, the files there now. |
| `results_workspace/warehouse/` | `warehouse.duckdb` (production) and `warehouse_dummy.duckdb` (dummy): the defaults for `etl`/`check`. |
| `results_workspace/dummy/<schema>/` | Dummy CSV, catalogue and EDA from `synth`. |
| `results_workspace/datasets/<dataset>/` | The dataset's own outputs: `constellation.md`, `patches/`, `tests/`, notes. |
| `results_workspace/process_log.jsonl` | One line per tool step (counts only); feeds the review page. |
| `results_workspace/token_log.jsonl` | One line per `olap-swifter` command: the size of what it printed, in estimated tokens (characters / 4). Feeds the README's token weight. |
| `results_workspace/experience/` | Past runs and their reports. |

**Where the results workspace lives.** Paths above that start with
`results_workspace/` are relative to the workspace, which sits outside the
skill:

1. `$OLAP_SWIFTER_WORKSPACE`, when set;
2. else `results_workspace/` in the folder the tools are run from, when that
   folder is not inside the skill (the usual case: the user's own project);
3. else (run from inside this repo, or with `OLAP_SWIFTER_ROOT` set)
   `results_workspace/` in the repo, git-ignored.

```bash
cd ~/my_project && /path/to/olap_swifter/.venv/bin/olap-swifter workspace --dataset hiv_site_b
#   -> ~/my_project/results_workspace/ with README.md and inputs/hiv_site_b/{catalogue,data,eda,codes,documents}/
olap-swifter workspace --zip      # -> results_workspace.zip beside it, for a chat download (raw data/ left out;
                                  #    --include-data puts it in; the warehouses always go in)
```

`OLAP_SWIFTER_ROOT` points the tools at another copy of the repo;
`OLAP_SWIFTER_SCHEMAS_DIR` at a different registry.

---

## 3. Run it as an agent (the full skill)

The skill ([SKILL.md](../../SKILL.md)) makes the decisions; these commands carry them
out. Every command takes `--json`, which is what the agent should read. Nothing
below prints a data row.

`O=".venv/bin/olap-swifter"` in the examples. An HIV dataset is used as the example.

**Step 1 — Collect the inputs.** Make the workspace and the dataset's inputs
folder first, and give the user its path (`$O workspace --dataset hiv_site_b`).
The user puts the files there (`OLAP_SWIFTER_INPUTS_DIR` overrides
`results_workspace/inputs/`):

```
results_workspace/inputs/hiv_site_b/
    catalogue/             (required) one or more dictionaries: visits.csv, viral_loads.yaml …
    data/                  one or more exports: visits.csv, viral_loads.csv …
    eda/                   optional: one EDA per data file, made from data/ if absent
    codes/                 optional: one file per code list, e.g. regimens.csv, icd10.csv
    documents/             optional: protocol, SOP, report (read only for the concept's answers)
    dataset_concept.yaml   written by describe(...) below
```

**EDA (Exploratory Data Analysis)**: a summary of each data file's shape
without its rows: column types, missing values, ranges, category counts. It is
how the skill models data it never reads. There are three ways to get one,
and the tools check for it first:

1. **The user gives one**: `eda/<data file stem>.yaml|.json|.csv`.
2. **ydata-profiling, in one line**, from the dataset's inputs folder:

   ```bash
   python -c "import pandas as pd; from ydata_profiling import ProfileReport; ProfileReport(pd.read_csv('data/visits.csv'), minimal=True, samples=None).to_file('eda/visits.json')"
   ```

   Install it in its own environment (`pip install ydata-profiling`): as of
   4.18 it needs pandas < 3, and this repo runs pandas 3. The tools read the
   JSON report, keeping only aggregates (category counts only for
   low-cardinality columns, small cells pooled). The report itself lists every
   value of every column, so the agent never opens it.
3. **Nothing**: `describe(...)` makes `eda/<file>.yaml` for every data file
   that has none (aggregates only, small categories suppressed).

An EDA is YAML (what the tools write by default) or a minimal CSV, one row per
variable: `variable,type,rows,missing,missing_pct,distinct,min,max,mean,categories,suppressed_rows`
(`categories` as `label:count|label:count`; only `variable` is required).
`source eda <csv> --out eda/file.csv` writes the CSV form.

A dataset often arrives as several exports and dictionaries; put them all in.
Dictionaries are merged by variable name, and a data file needs no matching
dictionary file of the same name.

Then the dataset concept is one line, with the four answers only the user can give:

```bash
.venv/bin/python -c "from olap_swifter import describe; describe('hiv_site_b', study='Adults on ART at four clinics, 2023-2025.', main_question='What share of patients are virally suppressed?', one_row_is='one viral load test', main_event='viral load test')"
```

It reads every dictionary in `catalogue/`, makes `eda/<file>.yaml` for each data
file that has none (aggregates only, small categories suppressed), lists every
file in `codes/` with its entry count (never the codes), and writes
`dataset_concept.yaml` (the dataset concept). It returns each data file's row and variable counts and its
source name, `<dataset>_<file>` (e.g. `hiv_site_b_viral_loads`).
It prints the required answers still empty. Re-running it keeps what the skill
has already filled in. Optional answers are flat keywords:

| Keyword | Template field |
| :--- | :--- |
| `codes` | `codes_and_vocabularies`, e.g. `codes={'regimen_code': 'regimens.csv'}`; unnamed files in `codes/` are listed with the file stem as the variable |
| `owner`, `period_covered` | `dataset.*` |
| `title`, `study_type`, `secondary_questions` | `study.*` |
| `main_measures`, `repeated_within_visit` | `unit_of_analysis.*` |
| `direct_identifiers`, `quasi_identifiers`, `eda_labels_allowed`, `other_languages` | `privacy.*` (`other_languages` → `other_language_vocabulary`) |
| `known_issues`, `instructions` | `known_issues`, `instructions_for_modeller` |

The CLI equivalent is `$O source describe hiv_site_b --study ... --main-question ...
--one-row-is ... --main-event ... --json`. Paths outside the folder are refused.

`visit`, `entities` and `relationship_to_existing` are not asked of the user:
the skill writes them into `dataset_concept.yaml` during Steps 2–3. `source match`
lists them under `skill_to_fill`, and `attach` / `extend` / `register` refuse a
dataset concept that still has `visit.identifier`, `relationship_to_existing.schema`
or `relationship_to_existing.decision` empty.

**Step 2 — Site vocabularies.** For a variable whose labels differ between
sites (wards, units, specimens…), list its labels and seed the crosswalk in
one line. Identifiers and free-text-like variables are refused:

```bash
$O source values amu_inpatient_survey ward --by hospital_code --crosswalk ward_type --json
```

It returns every label with its rows and the sites it appears at, and adds the
labels not yet in `results_workspace/schemas/crosswalks/ward_type.yaml` with an empty
`maps_to`. The agent fills each `maps_to` (see [§4](#crosswalk)); the load
refuses while any label is unmapped and names the missing ones.

**Steps 2–3 — Understand and match.** Catalogue and EDA are required; pass the
folders and every file in them is merged (each variable lists the data files it
appears in):

```bash
$O source match --catalogue results_workspace/inputs/hiv_site_b/catalogue \
                --eda results_workspace/inputs/hiv_site_b/eda \
                --concept results_workspace/inputs/hiv_site_b/dataset_concept.yaml --json
```

Read `recommendation` (`fit` / `extend` / `new_schema`), `best_schema`,
`tied_with`, and for the best candidate: `unmatched`, `blockers`, `code_gaps`,
`reusable_from_other_schemas`.

**Step 4a — Extend** (write a patch, see [§4](#4-file-formats)):

```bash
$O schema extend hiv_patch.yaml --dataset hiv_site_b \
    --catalogue results_workspace/inputs/hiv_site_b/catalogue --eda results_workspace/inputs/hiv_site_b/eda \
    --concept results_workspace/inputs/hiv_site_b/dataset_concept.yaml --main-fact fact_viral_load --json
```

**Step 4b — New schema** (write the full schema YAML, validate, register):

```bash
$O schema validate my_new_schema.yaml --json          # structure + conflicts with reused tables
$O schema register my_new_schema.yaml --dataset hiv_site_b --catalogue ... --eda ... --main-fact ... --json
```

**Step 5 — Fit** (nothing to add, just attach):

```bash
$O schema attach clinical_visit --dataset hiv_site_b --catalogue ... --eda ... --json
```

`extend`, `register` and `attach` all write the one schema file and return the
dataset's columns and the constellation tables it feeds.

**Step 6 — Dummy data: ETL, build, test.**

```bash
$O synth clinical_visit --json                     # capped dummy data -> results_workspace/dummy/clinical_visit/
$O etl clinical_visit results_workspace/dummy/clinical_visit/clinical_visit_synthetic.csv \
    --source clinical_visit_dummy --synthetic --json   # -> results_workspace/warehouse/warehouse_dummy.duckdb
$O build --csv results_workspace/dummy/clinical_visit/clinical_visit_synthetic.csv \
    --schema clinical_visit --source clinical_visit_dummy --json                     # validation, joins, slices, reviewer
```

Dummy data is capped at 400 rows (whole visits; `dummy_max_rows` in
`resources/config.yaml`). `synth` reports `visits`, `rows` and `capped`;
pass `--max-rows N` only when a test needs more volume. The agent reads these
JSON results, never the dummy CSV.

`etl` and `build --json` exit non-zero on any failed gate or check; `build --json`
returns counts only (table counts, check totals, failed checks, join distances,
slice depth). Then run the TEST.md sub-agents against
`results_workspace/warehouse/warehouse_dummy.duckdb`, and record their result on
the review page:

```bash
$O log dummy_tests --dataset hiv_site_b --passed 15 --total 15 --note "4 sub-agents"
```

Fix and repeat until all pass. Test scripts and patches the agent writes go in
`results_workspace/datasets/hiv_site_b/` (`scripts/`, `patches/`, `tests/`), never in the repo.

**Ask the user for the production data** before going on: every export must be in
`results_workspace/inputs/hiv_site_b/data/` and match the catalogue and EDA.

**Step 7 — Production data: ETL, build, test, debug, repeat.**

Run it once per data file, with the source name `describe` returned, in any
order:

```bash
for f in visits viral_loads; do
  $O etl clinical_visit results_workspace/inputs/hiv_site_b/data/$f.csv --source hiv_site_b_$f --json
done
$O build --csv results_workspace/inputs/hiv_site_b/data/viral_loads.csv --schema clinical_visit \
    --source hiv_site_b_viral_loads --json
$O check clinical_visit --source hiv_site_b_viral_loads --json
$O log production_tests --dataset hiv_site_b --passed 15 --total 15
```

Every source of the dataset (`hiv_site_b` or `hiv_site_b_<file>`) uses the
dataset's main fact for its coverage check.

Then run the TEST.md sub-agents on `results_workspace/warehouse/warehouse.duckdb`
(aggregates only) and `log production_tests` their result.
On a failure: a schema problem → patch with `schema extend`, rerun Step 6, then
this step; a data problem → report the counts to the user and wait for their
decision. Repeat until everything passes, then report back once (Step 8).

Use the dataset name (or `<dataset>_<file>`) as `--source`; the coverage check
then uses the dataset's own main fact. `--force` loads despite failed gates. Use it only with the
user's agreement.

**Step 8 — Report back when it is done:**

```bash
$O constellation --highlight hiv_site_b     # -> results_workspace/datasets/hiv_site_b/constellation.md
$O review --dataset hiv_site_b              # re-render model_reviewer.html for this dataset
```

The review page (`results_workspace/review/model_reviewer.html`) shows the schema art with the
dataset's tables filled, every workflow step done so far (✓ done, ✗ failed,
○ not yet) with its counts, and the last build's field mapping and checks.
The table picker above the field mapping switches between all tables and a
single table (its fields and join path only).

The Markdown has a Mermaid diagram with the dataset's tables marked ★ and a
table per warehouse table showing which datasets feed each column.

The report to the user is this view plus the dummy and production check and
TEST.md totals, the schema changelog entries made while debugging, and any data
issues with their counts.

---

## 4. File formats

### Schema (`results_workspace/schemas/<schema_id>.yaml`)

```yaml
schema_id: clinical_visit            # snake_case; the file name
title: General clinical visit warehouse
study: >                              # what the schema is for
  ...
spine: {table: dim_visit, key: visit_id}
main_fact: fact_encounters            # the schema's default main fact
excluded_variables:                   # accounted for, deliberately not warehoused
  patient_name: direct identifier
datasets:                             # written by extend / register / attach
  hiv_site_b: {catalogue: ..., eda: ..., main_fact: fact_viral_load, ...}
changelog: [...]                      # one line per additive change
tables:
  dim_patient:
    kind: dimension                   # dimension | fact
    key: patient_id                   # dimensions only
    links: [dim_other]                # dimensions this table references
    synthetic: {rows: 60}             # dummy-data entity count
    columns:
      patient_id: {type: VARCHAR, role: key, required: true, pii: true}
      gender: {type: VARCHAR, role: attribute, aliases: [sex], allowed: [Female, Male],
               datasets: [hiv_site_b]}
  fact_viral_load:
    kind: fact                        # always linked to the spine
    links: [dim_regimen]
    grain: [visit_id]                 # what identifies one row
    synthetic: {per_visit: [1, 1]}    # dummy rows per visit
    columns:
      viral_load_copies: {type: DOUBLE, role: measure, range: [0, 10000000]}
      art_start_year:                 # a per-variable extraction rule
        type: BIGINT
        role: attribute
        expr: "year(TRY_CAST(\"ART Start\" AS DATE))"   # DuckDB expression over raw columns
        inputs: ["ART Start"]         # raw variables it reads (counts toward zero variable loss)
        input_examples: ["2021-03-04"]  # used to generate dummy raw values
```

Types: `VARCHAR`, `BIGINT`, `DOUBLE`, `DATE`, `TIMESTAMP`, `BOOLEAN` (common
synonyms such as `string`, `integer`, `numeric` are accepted). Roles: `key`
(a dimension's own key only), `attribute`, `measure` (numeric). Every table also
gets `_source_id` (and facts `_source_row`) for lineage. `dim_data_source`
records each load: file, hash, schema, schema-file hash, synthetic flag, rows.

A table name means the same table in every schema. A new schema that uses an
existing table must agree with it on kind, key and column types.

### Extension patch

```yaml
base: clinical_visit
reason: "HIV ART monitoring: regimen and viral load"   # goes to the changelog
add_tables:      {dim_regimen: {...}, fact_viral_load: {...}}   # an existing table name is reused
add_columns:     {dim_patient: {art_start_date: {type: DATE, role: attribute}}}
add_aliases:     {dim_patient.gender: [sex]}
add_links:       {fact_viral_load: [dim_regimen]}
add_allowed_values: {fact_viral_load.vl_suppressed: ["Unknown"]}
add_excluded_variables: {patient_name: "direct identifier"}
```

Anything else (renames, retypes, removals) is refused.

### Crosswalk

A standard category column reads the site's label from another column:

```yaml
# in the schema
admission_ward: {type: VARCHAR, role: attribute, aliases: [ward]}     # the site's own label, kept as recorded
ward_type:
  type: VARCHAR
  role: attribute
  allowed: [Medical, Surgical, Paediatric, Maternity, ICU, Emergency, Outpatient, Other]
  crosswalk: {file: ward_type, from: admission_ward, scope: facility_id}
```

```yaml
# results_workspace/schemas/crosswalks/ward_type.yaml
name: ward_type
entries:
  - {value: Pediatric, maps_to: Paediatric}           # case and spacing are ignored
  - {value: Ward 4B, scope: HOSP-01, maps_to: Medical} # same name, different meaning per site:
  - {value: Ward 4B, scope: HOSP-03, maps_to: Surgical}#   one entry per site (scope wins)
```

`maps_to` must be one of the column's `allowed` values. The `crosswalk_coverage`
gate blocks the load while any label is unmapped and lists the labels (with
rows and site), the only check that shows values, because they are vocabulary.
Dummy data draws ward labels from the crosswalk's general entries.

### Catalogue

CSV or YAML, one entry per variable. Headers are matched loosely:
`variable`/`name`/`field`, `description`/`label`, `type`/`data_type`,
`values`/`codes`/`allowed_values` (separated by `|`, `;` or `,`), `units`.

### EDA

Any format is accepted and recorded for the agent to read. EDAs written by
`source eda` (YAML with `variables`) also feed their types and statistics into
matching.

---

## 5. Run it from Python

```python
import duckdb
from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.schema.profile import load_dictionary, load_eda, merge_profile_and_dictionary
from olap_swifter.schema.matcher import match_registry
from olap_swifter.schema.synthetic import write_synthetic_dataset
from olap_swifter.schema.materializer import WarehouseLoader

registry = SchemaRegistry()
variables = merge_profile_and_dictionary(load_eda("hiv_eda.yaml"), load_dictionary("hiv_catalogue.csv"))
print(match_registry(variables, registry)["recommendation"])

spec = registry.get("clinical_visit")
dummy = write_synthetic_dataset(spec, "results_workspace/dummy/clinical_visit", n_visits=200)
report = WarehouseLoader(duckdb.connect("results_workspace/warehouse/warehouse_dummy.duckdb")).load(
    spec, dummy["data"], "clinical_visit_dummy", is_synthetic=True)
print(report["preflight"]["all_passed"], report["postload"]["all_passed"])
```

The orchestrator wraps the same path and adds join analysis, cube slicing,
latency and the reviewer page:

```python
from olap_swifter.orchestrator import MedicalOLAPOrchestrator
orch = MedicalOLAPOrchestrator()
orch.ingest_synthetic_dataset(num_visits=200, schema_id="amr_surveillance")
result = orch.run_full_pipeline()
```

---

## 6. Command reference

| Command | What it does |
| :--- | :--- |
| `workspace [--dataset NAME] [--zip [--include-data]]` | Creates the results workspace (outside the skill) and its README, plus a dataset's inputs folder; prints the README and review-page paths to give the user. `--zip` packs it for download. |
| `source describe <name> --study --main-question --one-row-is --main-event` | `results_workspace/inputs/<name>/dataset_concept.yaml` (the dataset concept; + EDA if missing). Same as `describe(...)`. |
| `source eda <csv or folder> --out <yaml/csv or folder> [--csv]` | Aggregate-only EDA (small categories suppressed), one per CSV; `.csv`/`--csv` for the minimal CSV form. |
| `source values <dataset> <variable> [--by SITE_VAR] [--crosswalk NAME]` | Distinct labels of a vocabulary variable with rows and sites; merges new labels into a crosswalk. |
| `source match --catalogue C --eda E [--concept D]` | Scores against every schema: `fit` / `extend` / `new_schema`. |
| `schema list` / `schema show <id>` | Registered schemas. |
| `schema validate <yaml>` | Checks a schema written by the agent, including reused-table conflicts. |
| `schema register <yaml> --dataset --catalogue --eda [--concept] [--main-fact]` | Adds a new schema and attaches its dataset. |
| `schema extend <patch> --dataset --catalogue --eda [--concept] [--main-fact]` | Additive change in place, then attach. |
| `schema attach <id> --dataset --catalogue --eda [--concept] [--main-fact]` | Attach a dataset that fits as-is. |
| `build [--csv C --schema S --source N] [--json]` | ETL + validation, join paths, slices, reviewer page. `--json`: counts only. |
| `synth <id> [--out-dir] [--visits] [--max-rows]` | Dummy CSV + catalogue + EDA from a schema, capped at 400 rows (default: `results_workspace/dummy/<id>/`). |
| `etl <id> <csv> --source S [--db DB] [--synthetic] [--force]` | Stage → gates → load → checks. Counts only. DB defaults to the workspace warehouse (dummy one with `--synthetic`). |
| `check <id> --source S [--db DB] [--synthetic]` | Re-run all gates and checks for a loaded source. |
| `constellation [--highlight DATASET] [--out md]` | The one warehouse view, a dataset highlighted (written to the dataset's workspace folder). |
| `log <stage> --dataset D [--passed N --total M] [--failed] [--note] [--tokens N]` | Record a step the tools cannot see (TEST.md results) on the review page. `--tokens` records the agent's own token use as the host reports it; the README shows the latest. |
| `profile [--schema]` | Join paths, deepest cube slices, latency. |
| `review [--dataset]` / `verify-ui` | Re-render the review page / Playwright check of it. |
| `ocr <image>` | OCR + de-identification (see [OCR.md](OCR.md); kept separate from the warehouse for now). |

All are `.venv/bin/olap-swifter <command>`, or `.venv/bin/python -m olap_swifter.cli <command>`.

---

## 7. Test the code tools

Each module can be exercised on its own.

### The builder — `DynamicOLAPBuilder`
Keeps the field inventory shown in the reviewer page. In the schema workflow it
is filled from the schema (`load_mappings_from_schema`); its keyword-based
guessing below is only a hint for datasets with no catalogue yet.

```python
import pandas as pd
from olap_swifter.builder import DynamicOLAPBuilder

df = pd.read_csv("resources/datasets/sample_wide_clinical.csv")
builder = DynamicOLAPBuilder()
builder.register_raw_data(df, table_name="raw_clinical_records")

# No column left behind
all_mapped, missing = builder.verify_no_variable_left_out()
assert all_mapped is True and len(missing) == 0

# Move a field to a different table
builder.switch_field("gender", "dim_patient_demographics", new_role="dimension_attribute")
inv = {f["name"]: f for f in builder.get_field_inventory()}
assert inv["gender"]["target_table"] == "dim_patient_demographics"
```

### The validator — `SemanticDataValidator`
Checks row counts and metric totals against the raw staging table.

```python
from olap_swifter.validator import SemanticDataValidator

builder.generate_olap_tables("constellation")
validator = SemanticDataValidator(conn=builder.conn, raw_table="raw_clinical_records")

sum_res = validator.validate_metric_sum("encounter_cost_ugx", "fact_encounters")
assert sum_res.passed is True

ref_res = validator.validate_referential_integrity("fact_encounters", "dim_visit", "visit_id")
assert ref_res.passed is True
```

### The cube and slicer — `MedicalCube`, `CubeSlicer`
Checks multi-dimensional slicing, roll-ups and finding the deepest populated
slices, on dummy data from a registered schema.

```python
from olap_swifter.olap.store import MedicalOLAPStore
from olap_swifter.olap.cube import MedicalCube
from olap_swifter.profiler.slicer import CubeSlicer

store = MedicalOLAPStore()
store.populate_synthetic_data(num_visits=200, schema_id="clinical_visit")
cube = MedicalCube(conn=store.conn)
slicer = CubeSlicer(conn=store.conn)

slice_res = cube.slice_and_dice(
    measures=["COUNT(*) AS visit_count", "SUM(e.encounter_cost_ugx) AS total_cost"],
    group_by_dimensions=["v.visit_type", "v.age_group"],
    from_table="dim_visit v",
    joins=["JOIN fact_encounters e ON v.visit_id = e.visit_id"],
)
assert len(slice_res) > 0

deep_slices = slicer.dig_deepest_slices(
    base_from_clause=(
        "fact_encounters e "
        "JOIN dim_visit v ON e.visit_id = v.visit_id "
        "JOIN dim_patient p ON v.patient_id = p.patient_id"
    ),
    candidate_dimensions=["v.visit_type", "v.age_group", "p.gender"],
    metric_sql="COUNT(*)",
    min_depth=2,
    max_depth=3,
)
assert len(deep_slices) > 0
```

### The join graph and latency profiler — `JoinGraph`, `LatencyProfiler`
Checks shortest join paths and query timings. Relationships come from the
schema YAML.

```python
from olap_swifter.profiler.join_graph import JoinGraph
from olap_swifter.olap.constellation import get_constellation_schema_metadata
from olap_swifter.profiler.latency import LatencyProfiler

graph = JoinGraph()
graph.load_from_metadata(get_constellation_schema_metadata("clinical_visit"))
assert graph.get_join_path("fact_prescriptions", "dim_facility") == \
    ["fact_prescriptions", "dim_visit", "dim_facility"]

profiler = LatencyProfiler(conn=store.conn)
profile_res = profiler.profile_query("SELECT COUNT(*) FROM fact_encounters", label="Encounter Count")
assert profile_res.total_duration_ms >= 0.0
```

---

## 8. Run the full test suite

Everything at once:

```bash
.venv/bin/pytest -v
```

Or one module at a time:

```bash
# Legacy keyword builder on the sample datasets
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_dynamic_datasets.py

# Zero variable loss and semantic validation
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_builder_and_validator.py

# Cube slicing, roll-ups and pivots
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_cube.py

# Join graph distances and latency profiling
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_profiler.py

# The full end-to-end pipeline
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_orchestrator.py

# Schema workflow: registry, match, extend, dummy data, ETL gates, constellation view
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_schema_workflow.py
```

---

## 9. The datasets

| File | Rows | Columns | Schema |
| :--- | :--- | :--- | :--- |
| `sample_wide_clinical.csv` | 158 | 26 | `clinical_visit` (`fit`) |
| `sample_amr_clinical.csv` | 200 | 22 | `amr_surveillance` (`fit`) |

Both samples match their schemas but **fail the preflight gates**, and they
should: the generator that made them randomised values that ought to be stable.

- `sample_amr_clinical.csv`: 49 of 60 patients have more than one gender; every patient has more than one home district.
- `sample_wide_clinical.csv`: `prior_antibiotic_use_30d` and `substance_use_history` differ between rows of the same visit (28 and 32 visits); `drug_cost_ugx` has two values for the same visit and drug (12 cases).

Use them to see the gates work. Use `synth` for a clean end-to-end run.
