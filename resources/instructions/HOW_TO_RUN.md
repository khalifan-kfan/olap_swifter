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
| `resources/code_tools/privacy_tools/` | Presidio de-identification (offline; English only). |
| `results_workspace/review/model_reviewer.html` | The review page. Rewritten after every tool step. Git-ignored. |
| `results_workspace/` | **The results workspace: everything a run writes.** Outside the skill (see below). `README.md` is its key: the review page link, the folder structure, the decisions made per dataset, the files there now. |
| `results_workspace/warehouse/` | `warehouse.duckdb` (production) and `warehouse_dummy.duckdb` (dummy): the defaults for `etl`/`check`. They hold generated keys only (`PAT-000001`…), never a source identifier. |
| `results_workspace/warehouse/*_staging.duckdb` | **Private.** Beside each warehouse: the staged raw sources (`stg_<source>`, the test oracle) and `_keymap` (source identifier → generated key). Attached as `stg` during loads; never shared, never zipped (unless `--include-data`), hidden from `view`. |
| `results_workspace/dummy/<schema>/` | Dummy CSV, catalogue and EDA from `synth`. |
| `results_workspace/datasets/<dataset>/` | The dataset's own outputs: `constellation.md`, `patches/`, `scripts/`, `tests/`, `data_issues/<source>.json` (counts, listed in the README for cleaning), `documents_text/` (long source documents as text), notes. |
| `results_workspace/process_log.jsonl` | One line per tool step (counts only); feeds the review page. |
| `results_workspace/token_log.jsonl` | One line per `olap-swifter` command: the size of what it printed, in estimated tokens (characters / 4). Feeds the README's token weight. |

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
    data/                  one or more exports: visits.csv, labs.xlsx …
    eda/                   optional: one EDA per data file, made from data/ if absent
    codes/                 optional: one file per code list, e.g. regimens.csv, icd10.xlsx
    documents/             optional: protocol, SOP, report (read only for the concept's answers)
    dataset_concept.yaml   written by describe(...) below
```

Accepted files per folder (anything else is reported as not read):

| Folder | Accepted | Notes |
| :--- | :--- | :--- |
| `data/` | `.csv` `.xlsx` `.xls` | Every non-empty sheet of a workbook is its own export: `labs.xlsx#Results` (source `<dataset>_labs__Results`); a one-sheet workbook is just `labs.xlsx`. Sheets are converted to hidden all-text CSVs in `data/.sheets/` for the tools. |
| `eda/` | `.yaml` `.yml` `.json` `.csv` `.xlsx` `.xls` | One per data file, named after it (`visits.yaml`, `labs__Results.xlsx`). |
| `catalogue/` | `.csv` `.yaml` `.yml` `.xlsx` `.xls` `.docx` `.pdf` | Excel: every sheet. Word and PDF: every table whose header has a variable/name column (tables continuing over a PDF page break are joined). |
| `codes/` | `.csv` `.yaml` `.yml` `.txt` `.xlsx` `.xls` `.docx` `.pdf` | One code list per file; only entry counts are read. |
| `documents/` | `.pdf` `.docx` `.txt` `.md` | Read for the concept's answers only. Longer than three pages: extracted to text first (below). |

**EDA (Exploratory Data Analysis)**: a summary of each data file's shape
without its rows: column types, missing values, ranges, category counts. It is
how the skill models data it never reads.

It is also what the dummy data is shaped by: `synth` reads the attached
datasets' EDAs so dummy rows have the real null rates, ranges, category
proportions and entity counts (inside the schema's rules). There are three
ways to get one, and the tools check for it first:

1. **The user gives one**: `eda/<data file stem>` as `.yaml`, `.json`, `.csv`, `.xlsx` or `.xls`.
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

An EDA is YAML (what the tools write by default) or a minimal CSV or Excel sheet, one row per
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
file in `codes/` with its entry count (never the codes), lists `documents/`
with page and word counts, and writes `dataset_concept.yaml` (the dataset
concept). Files of a type a folder does not accept are returned under
`unsupported_files`.

**Long source documents.** A document longer than three pages is extracted to
`results_workspace/datasets/<dataset>/documents_text/<name>.txt`, with
`===== page N =====` markers, and its entry in the concept's `documents:`
list links each template field to the pages most likely to answer it:

```yaml
documents:
- file: documents/protocol.pdf
  pages: 42
  words: 15310
  text: .../datasets/hiv_site_b/documents_text/protocol.txt
  concept_links: {study.main_question: [3, 4], privacy: [17], unit_of_analysis.one_row_is: [9]}
```

Read those pages of the text for the answers, nothing else. Shorter documents
are read as they are. It returns each data file's row and variable counts and its
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
| `placement` | `placement`: the user's choice if the dataset overlaps a built schema, `extend`, `own_mart` or `ask_me`. `schema extend` and `schema attach` refuse unless it is `extend`; `schema register` with `own_mart` refuses to reuse another schema's tables. |

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
`resources/config.yaml`) and shaped by the attached datasets' EDAs
(`shaped_by_eda_variables` in the result says how many variables it used).
`synth` reports `visits`, `rows` and `capped`;
pass `--max-rows N` only when a test needs more volume. The agent reads these
JSON results, never the dummy CSV.

Every check has a severity. `error` means the transfer would break (a variable
unaccounted for, a missing or blank key, a value that cannot be cast, a derived
value that cannot be computed, an unmapped label, a value that differs between
source and warehouse): the load is refused and the command exits non-zero.
`data_issue` means the source itself has a problem (out of range, outside the
vocabulary, a required value missing, an entity recorded two ways): the load
goes ahead with the values as recorded, the result lists them under
`data_issues`, and they are written to `datasets/<dataset>/data_issues/` and
the README's "Data issues for cleaning". `build --json` returns counts only
(table counts, check totals, failed checks, join distances, slice depth).

Every dimension key is generated during the load (`generated_keys` in the
result counts the new ones per dimension); the source identifiers go to the
key map in the staging database. To query staging and the key map (tests,
counts only):

```python
con = duckdb.connect("results_workspace/warehouse/warehouse_dummy.duckdb", read_only=True)
con.execute("ATTACH 'results_workspace/warehouse/warehouse_dummy_staging.duckdb' AS stg (READ_ONLY)")
# stg.stg_<source> = raw rows;  stg._keymap(dimension, natural_key, surrogate_key, source_id)
``` Then run the TEST.md sub-agents against
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
On an `error`: fix the schema so it can hold the value as recorded (a schema
problem → patch with `schema extend`), rerun Step 6, then this step. The ETL is
usually the longest part, and each dataset has its own issues: write any
dataset-specific helper in `results_workspace/datasets/<dataset>/scripts/`.
Data issues need no fix: they are loaded as recorded and reported (README).
Repeat until everything passes, then report back once (Step 8).

Use the dataset name (or `<dataset>_<file>`) as `--source`; the coverage check
then uses the dataset's own main fact. `--force` loads despite a failed
transfer gate. Use it only with the user's agreement: the values it could not
transfer are lost.

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
TEST.md totals, the schema changelog entries made while debugging, and the
data issues for cleaning from the README, with their counts.

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
  dim_regimen:
    kind: dimension
    key: regimen_code
    columns:
      regimen_code: {type: VARCHAR, role: key, keep_as: regimen}   # a code that means something: the
                                      # warehouse key is generated, `regimen` keeps the source value
                                      # (never allowed on a pii key; the attribute is implied, not written)
  dim_patient:
    kind: dimension                   # dimension | fact
    key: patient_id                   # dimensions only; its values become generated keys (PAT-000001)
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

spec = registry.get("amr_surveillance")   # any registered schema
dummy = write_synthetic_dataset(spec, "results_workspace/dummy/amr_surveillance", n_visits=200)
report = WarehouseLoader(duckdb.connect("results_workspace/warehouse/warehouse_dummy.duckdb")).load(
    spec, dummy["data"], "amr_surveillance_dummy", is_synthetic=True)
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
| `source eda <data file or folder> --out <yaml/csv or folder> [--csv]` | Aggregate-only EDA (small categories suppressed), one per CSV or Excel sheet; `.csv`/`--csv` for the minimal CSV form. |
| `source values <dataset> <variable> [--by SITE_VAR] [--crosswalk NAME]` | Distinct labels of a vocabulary variable with rows and sites; merges new labels into a crosswalk. |
| `source match --catalogue C --eda E [--concept D]` | Scores against every schema: `fit` / `extend` / `new_schema`. |
| `schema list` / `schema show <id>` | Registered schemas. |
| `schema validate <yaml>` | Checks a schema written by the agent, including reused-table conflicts. |
| `schema register <yaml> --dataset --catalogue --eda [--concept] [--main-fact]` | Adds a new schema and attaches its dataset. |
| `schema extend <patch> --dataset --catalogue --eda [--concept] [--main-fact]` | Additive change in place, then attach. |
| `schema attach <id> --dataset --catalogue --eda [--concept] [--main-fact]` | Attach a dataset that fits as-is. |
| `build [--csv C --schema S --source N] [--json]` | ETL + validation, join paths, slices, reviewer page. `--json`: counts only. |
| `synth <id> [--out-dir] [--visits] [--max-rows]` | Dummy CSV + catalogue + EDA from a schema, shaped by the attached datasets' EDAs, capped at 400 rows (default: `results_workspace/dummy/<id>/`). |
| `etl <id> <csv or xlsx[#sheet]> --source S [--db DB] [--synthetic] [--force]` | Stage (private staging DB) → gates → generated keys → load → checks. Counts only; data issues reported, never blocking. DB defaults to the workspace warehouse (dummy one with `--synthetic`). |
| `check <id> --source S [--db DB] [--synthetic]` | Re-run all gates and checks for a loaded source. |
| `constellation [--highlight DATASET] [--out md]` | The one warehouse view, a dataset highlighted (written to the dataset's workspace folder). |
| `log <stage> --dataset D [--passed N --total M] [--failed] [--note] [--tokens N]` | Record a step the tools cannot see (TEST.md results) on the review page. `--tokens` records the agent's own token use as the host reports it; the README shows the latest. |
| `profile [--schema]` | Join paths, deepest cube slices, latency. |
| `review [--dataset]` / `verify-ui` | Re-render the review page / Playwright check of it. |

All are `.venv/bin/olap-swifter <command>`, or `.venv/bin/python -m olap_swifter.cli <command>`.

---

## 7. Test the code tools

Each module can be exercised on its own.

### The field inventory — `DynamicOLAPBuilder`
Where every raw variable of a staged source goes, from the schema. It is how
the build proves zero variable loss and what the review page shows.

```python
import duckdb
from olap_swifter.schema.materializer import WarehouseLoader, resolve_source
from olap_swifter.schema.registry import SchemaRegistry
from olap_swifter.builder import DynamicOLAPBuilder

conn = duckdb.connect()
spec = SchemaRegistry().get("amr_surveillance")
report = WarehouseLoader(conn).load(spec, "resources/datasets/sample_amr_clinical.csv", "sample_amr")
builder = DynamicOLAPBuilder(conn)
builder.load_mappings_from_schema(spec, resolve_source(spec, conn, report["staging_table"]))
assert builder.verify_no_variable_left_out_of(report["staging_table"]) == (True, [])
```

Source-versus-warehouse parity is checked by the loader itself (`value_parity`,
`measure_sum_parity`, `derived_parity`, `orphan_keys`: see `etl --json`).

### The cube and slicer — `MedicalCube`, `CubeSlicer`
Checks multi-dimensional slicing, roll-ups and finding the deepest populated
slices, on dummy data from a registered schema.

```python
from olap_swifter.olap.store import MedicalOLAPStore
from olap_swifter.olap.cube import MedicalCube
from olap_swifter.profiler.slicer import CubeSlicer

store = MedicalOLAPStore()
store.populate_synthetic_data(num_visits=200, schema_id="amr_surveillance")
cube = MedicalCube(conn=store.conn)
slicer = CubeSlicer(conn=store.conn)

slice_res = cube.slice_and_dice(   # keys are generated, so joins go through them
    measures=["COUNT(*) AS result_count", "AVG(a.mic_ug_ml) AS avg_mic"],
    group_by_dimensions=["a.ast_interpretation", "v.age_group"],
    from_table="fact_ast_results a",
    joins=["JOIN dim_visit v ON a.visit_id = v.visit_id"],
)
assert len(slice_res) > 0

deep_slices = slicer.dig_deepest_slices(
    base_from_clause=(
        "fact_ast_results a "
        "JOIN dim_visit v ON a.visit_id = v.visit_id "
        "JOIN dim_patient p ON v.patient_id = p.patient_id"
    ),
    candidate_dimensions=["a.ast_interpretation", "v.age_group", "p.gender"],
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
graph.load_from_metadata(get_constellation_schema_metadata("amr_surveillance"))
assert graph.get_join_path("fact_ast_results", "dim_facility") == \
    ["fact_ast_results", "dim_visit", "dim_facility"]

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
# Cube slicing, roll-ups and pivots
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_cube.py

# Join graph distances and latency profiling
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_profiler.py

# The full end-to-end pipeline
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_orchestrator.py

# Schema workflow: registry, match, extend, generated keys, data issues, keep_as, crosswalks, constellation
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_schema_workflow.py

# Full transfer and inputs: Excel/Word/text inputs, long documents, derived and value parity,
# EDA-shaped dummy data, the user's placement, staging never shared, data issues in the README
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_transfer_and_inputs.py

# The results workspace: location, README, zip, token weight
.venv/bin/pytest -v resources/code_tools/olap_swifter/tests/test_workspace.py
```

The tests run in a throwaway root, on one fixture schema (`tests/fixtures/schemas/amr_surveillance.yaml`);
they never touch your registry or workspace.

---

## 9. The datasets

| File | Rows | Columns | Schema |
| :--- | :--- | :--- | :--- |
| `sample_amr_clinical.csv` | 200 | 22 | `amr_surveillance` (`fit`) |

The sample matches its schema and loads, but reports **data issues**, and it
should: the generator that made it randomised values that ought to be stable.

- `sample_amr_clinical.csv`: 49 of 60 patients have more than one gender; every patient has more than one home district.

Use it to see data issues reported. Use `synth` for a clean end-to-end run.
