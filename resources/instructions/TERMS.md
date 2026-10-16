# Terms

Every term below is used in [SKILL.md](../../SKILL.md), [HOW_TO_RUN.md](HOW_TO_RUN.md)
and [TEST.md](../../TEST.md) with this meaning. Use the same words with the
user, and explain one the first time it comes up.

**The data and what describes it**

- **Dataset** — one study's or programme's data: one or more **data exports**
  (raw CSV files) plus the files that describe them. A **variable** is one
  column of an export.
- **Catalogue** (data dictionary) — the data owner's list of variables: name,
  meaning, type, allowed codes.
- **EDA** (Exploratory Data Analysis) — a summary of a data file's shape
  without its rows: column types, missing values, ranges, category counts.
- **Dataset concept** — `dataset_concept.yaml`: what the dataset is (study,
  main question, what one row is, main event, privacy), answered by the user,
  plus how the agent modelled it (visit, entities, relationship to existing
  schemas).
- **Code list / vocabulary** — a fixed set of allowed codes or labels for a
  variable: ICD-10 diagnoses, regimen codes, ward names.
- **Production data** — the real export. **Dummy data** — made-up rows
  generated from a schema, to prove the schema before any real data loads.

**Privacy**

- **Direct identifier** — a value that names a person on its own: name, phone
  number, national ID, medical record number.
- **Quasi-identifier** — harmless alone, identifying in combination: date of
  birth, village, a rare condition, a very small facility.
- **Small cell** — a category with so few rows (under 5 by default) that its
  count could point to a person. The tools pool small cells as "suppressed".
- **Presidio** — the de-identification library the OCR tools use; English only.

**The warehouse**

- **OLAP warehouse** (Online Analytical Processing) — a database shaped for
  counting, summing and slicing, not for day-to-day record keeping. Here, a
  DuckDB file.
- **Fact table** (`fact_…`) — one row per event or measurement: a lab test, a
  prescription, a survey interview. Its numbers are **measures**.
- **Dimension table** (`dim_…`) — the things events are about: patient,
  facility, visit. Its descriptive columns (**attributes**) stay the same for
  that thing.
- **Key** — a column that identifies one row of a dimension; facts point to
  dimensions through keys.
- **Grain** — exactly what one row of a fact table is ("one viral load test
  at one visit").
- **Main fact** — the fact table for the dataset concept's main event.
- **Visit spine** — the visit dimension (`dim_visit`); every fact links to a
  visit (SKILL.md, Rule 5).
- **Fact constellation** — many fact tables sharing the same dimensions: the
  shape of the whole warehouse (SKILL.md, Section 2).
- **Data mart** — a self-contained set of tables for a dataset that shares too
  little with the constellation to join it.
- **Fan-out** — rows multiplied by a join, which inflates totals.

**Schemas and how they change**

- **Schema** — one YAML file describing a set of tables (columns, types,
  rules) and the datasets attached to it.
- **Registry** — every schema built so far; it lives in the results workspace.
- **Match** — scoring a dataset against every schema in the registry. The
  **decision** is `fit` (attach as-is), `extend` (add to a schema) or
  `new_schema` (SKILL.md, Step 3).
- **Patch** — a file of additive changes to an existing schema; each change is
  written, with its reason, to the schema's **changelog**.
- **Attach** — record on a schema that a dataset feeds it, and which columns.
- **Alias** — another name for a schema column, so a source's `gender` loads
  into the schema's `sex` without renaming anything.
- **Expression** — a rule written into the schema that derives or cleans a
  value (birth year from date of birth, a visit id from patient plus date). The
  tools apply it and report counts; the agent never sees the values.
- **Site label** — a value exactly as one site recorded it ("Paeds Ward 2").
  **Standard category** — the shared value it means ("Paediatrics").
- **Crosswalk** — a lookup table that maps each site label to a standard
  category, per site, so data from different sites can be compared. The site
  label is kept; the standard category is added beside it (SKILL.md, Rule 7).
- **Values tool** — lists a vocabulary variable's distinct labels with counts
  and the sites they appear at, so the agent can write a crosswalk. Never run on
  identifiers or free text.

**Running it**

- **ETL** (Extract, Transform, Load) — read an export, apply the schema's
  rules, load it into the warehouse.
- **Staging** — the export loaded as-is into one table before it is split; the
  warehouse's totals are checked against it.
- **Gate** — a check before loading that refuses to load when something is
  wrong (a variable unaccounted for, a null key, an unmapped label).
  **Post-load check** — a check after loading that compares the warehouse with
  staging.
- **Build** — the pipeline after loading: validation, join paths, slices and
  the review page.
- **Sub-agents** — separate agents that each test the warehouse against one
  **mandate** in `TEST.md`.
- **Results workspace** — the folder, outside the skill, where everything a run
  writes goes. Its README is the **results key**.
- **Token weight** — how heavy the skill is, in tokens (the units a language
  model reads and writes; roughly 4 characters each). The results README
  shows it: the skill's documents, the output of every tool command, and the
  agent's own total when it records one.
- **Review page** — `model_reviewer.html` in the results workspace: schema art,
  every step done so far, the field mapping and the checks.
