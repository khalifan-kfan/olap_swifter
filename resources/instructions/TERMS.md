# Terms

Every term below is used in [SKILL.md](../../SKILL.md), [HOW_TO_RUN.md](HOW_TO_RUN.md)
and [TEST.md](../../TEST.md) with this meaning. Use the same words with the
user, and explain one the first time it comes up.

**The data and what describes it**

- **Dataset** — one study's or programme's data: one or more **data exports**
  (raw CSV or Excel files; each Excel sheet counts as one export) plus the
  files that describe them. A **variable** is one column of an export.
- **Catalogue** (data dictionary) — the data owner's list of variables: name,
  meaning, type, allowed codes.
- **EDA** (Exploratory Data Analysis) — a summary of a data file's shape
  without its rows: column types, missing values, ranges, category counts.
  The agent models from it, and the tools shape **dummy data** with it (null
  rates, ranges, category proportions, entity counts), so dummy data looks
  like the real export without anyone reading it.
- **Dataset concept** — `dataset_concept.yaml`: what the dataset is (study,
  main question, what one row is, main event, privacy), answered by the user,
  plus how the agent modelled it (visit, entities, relationship to existing
  schemas).
- **Code list / vocabulary** — a fixed set of allowed codes or labels for a
  variable: ICD-10 diagnoses, regimen codes, ward names.
- **Source document** — a protocol, SOP or report in `documents/` (PDF, Word,
  text), read only for the dataset concept's answers. One longer than three
  pages is extracted to text first, with its pages linked to the template
  fields they answer.
- **Production data** — the real export. **Dummy data** — made-up rows
  generated from a schema and the dataset's EDA, to prove the schema before
  any real data loads.

**Privacy**

- **Direct identifier** — a value that names a person on its own: name, phone
  number, national ID, medical record number.
- **Quasi-identifier** — harmless alone, identifying in combination: date of
  birth, village, a rare condition, a very small facility.
- **Small cell** — a category with so few rows (under 5 by default) that its
  count could point to a person. The tools pool small cells as "suppressed".
- **Presidio** — the offline de-identification library in `privacy_tools`; it
  masks names, phone numbers and IDs in text. English only.

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
- **Generated key** (surrogate key) — the key the ETL makes for every entity
  (`PAT-000001`, `FAC-000001`, `VIS-000001`). The warehouse never holds the
  source's own identifiers (patient numbers, facility codes, derived visit
  ids); they stay in the **key map** (`_keymap`) in the private staging
  database, which links each source identifier to its generated key. A key
  that is a **code that means something** (a regimen or ICD code) also keeps
  its value as an attribute (`keep_as`); a person identifier never does.
- **Grain** — exactly what one row of a fact table is ("one viral load test
  at one visit").
- **Main fact** — the fact table for the dataset concept's main event.
- **Visit** / **Encounter** — the same thing, two names: one contact between a
  patient and the health service during which events happen (a clinic visit,
  an admission, a home or outreach visit, a test sample). Use the word the
  data's people use. Every diagnosis, drug, test or measurement belongs to one.
- **Spine table** — the dimension every fact joins to: `dim_visit`, one row
  per visit (encounter). Its key (the **spine key**, `visit_id`) is a
  variable in the source, or derived (e.g. patient + date); in the warehouse
  it is a generated key (`VIS-000001`).
- **Visit (encounter) spine** — the model built around the spine table: every
  fact links to a visit, and facts join each other only through it, never
  directly (SKILL.md, Rule 5).
- **Fact constellation** — many fact tables sharing the same dimensions: the
  shape of the whole warehouse (SKILL.md, Section 2).
- **Data mart** — a self-contained set of tables for a dataset, beside the
  constellation rather than in it.
- **Placement** — the user's choice, in the dataset concept, for a dataset that
  overlaps a schema already built: **extend** it (share its tables), make the
  dataset its **own data mart**, or **ask me** once the match is known. The
  agent never makes this choice alone.
- **Fan-out** — rows multiplied by a join, which inflates totals. It happens
  when one row is matched to several rows in another table, so the first row
  is repeated once per match. Example: a visit with one weight (70 kg) and
  three lab tests. Joining visits to lab tests gives three rows, each carrying
  70 kg, so a sum of weights says 210 kg and a count of visits says 3. Flat
  exports have the same problem built in: visit-level values are repeated on
  every row of the visit. The fix is to give every fact a **grain**, take each
  measure from its own table at that grain, and join facts through the visit
  spine rather than to each other.

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
- **Expression** — a rule written into the schema that derives a value from
  raw variables (birth year from date of birth, a visit id from patient plus
  date). It derives; it never cleans. The tools apply it and report counts;
  the agent never sees the values. A **derived feature** is a column made this
  way (or by a crosswalk); its value is checked against the rule applied to
  the original variables.
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
  rules, load it into the warehouse. Usually the longest step: every dataset
  brings its own issues.
- **Full transfer** — the aim of the ETL: every value of the source arrives in
  the warehouse exactly as recorded, cast to its type or derived by a stated
  rule. Nothing is cleaned, dropped or corrected on the way.
- **Staging** — the export loaded as-is (all text) into one table before it is
  split; the warehouse is checked against it. Staged tables live in the
  private **staging database** (`<warehouse>_staging.duckdb`) with the key
  map, never in the warehouse, and are never shared.
- **Gate** — a check before loading that refuses to load when the transfer
  would break (a variable unaccounted for, a missing key, a value that cannot
  be cast or derived, an unmapped label). **Post-load check** — a check after
  loading that compares the warehouse with staging, value for value.
- **Data issue** — a problem in the source itself: a value outside its range,
  a code outside the vocabulary, a required value missing, an entity recorded
  two ways. It never blocks the load: the value is transferred as recorded,
  and the issue is listed for cleaning in the results README (`DATA_ISSUE` in
  `TEST.md`).
- **Build** — the pipeline after loading: validation, join paths, slices and
  the review page.
- **Sub-agents** — separate agents that each test the warehouse against one
  **mandate** in `TEST.md`.
- **Results workspace** — the folder, outside the skill, where everything a run
  writes goes. Its README is the **results key**, and lists the data issues
  for cleaning.
- **Token weight** — how heavy the skill is, in tokens (the units a language
  model reads and writes; roughly 4 characters each). The results README
  shows it: the skill's documents, the output of every tool command, and the
  agent's own total when it records one.
- **Review page** — `model_reviewer.html` in the results workspace: schema art,
  every step done so far, the field mapping and the checks.
