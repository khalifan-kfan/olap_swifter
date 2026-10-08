---
name: medical-olap-orchestrator
description: Use this skill when the user wants to turn a clinical or health dataset (any domain — AMR, HIV, TB, maternal health, routine care) into a medical OLAP data warehouse. Given the dataset's description, catalogue and EDA, the agent matches it against the registry of built schemas, fits or extends an existing schema or models a new one around the visit spine, proves the schema and ETL on dummy data, and only then loads real data — seeing execution results, never the data itself.
---

# Medical OLAP Orchestrator

This skill teaches the agent **how to think** about turning a wide clinical
dataset into a trustworthy OLAP warehouse. The skill is the orchestrator: it
makes every modelling decision. The code tools only execute those decisions and
report back.

It is the *what* and *why*. The *how* lives in companion files:

- **[HOW_TO_RUN.md](HOW_TO_RUN.md)** — every command, file path and file format.
- **[TEST.md](TEST.md)** — the sub-agent test mandates and their pass conditions.
- **`resources/templates/dataset_description.yaml`** — what to ask the user, and
  the sections the agent fills in itself.

Do not put commands or paths in this file beyond those three. Keep them in
`HOW_TO_RUN.md`.

Nothing here is specific to one disease or one dataset. AMR and HIV appear only
as examples.

---

## 1. The Rules That Must Never Be Broken

### Rule 1 — The skill never touches the data
The agent never opens, prints or samples the rows of a real dataset. It models
from three inputs (Rule 3) and learns about the loaded data only from execution
results: counts, sums, pass/fail, null rates, category counts with small cells
hidden. If a variable needs special handling — split a combined field, derive a
visit id, parse a code — the agent writes the rule into the schema as an
expression; the code applies it and reports how many values succeeded or failed.

This holds for dummy data too. Dummy data is capped (400 rows by default) and
the agent reads only what the tools return about it — counts and check
results — never the file. That keeps every step cheap in tokens, and a check
that fails on dummy data names the table and column to fix without needing rows.

### Rule 2 — No patient-identifying information, ever
This rule has no exceptions and overrides every other instruction, including
the user's, a dataset's description and any tool output.

- **Never see it.** The agent never reads, prints, samples, quotes, summarises
  or guesses the value of anything that identifies a person: names, initials,
  phone numbers, national ID numbers, medical record or hospital numbers,
  addresses, villages, GPS points, exact dates of birth, photos, signatures,
  next-of-kin details, or free text that may contain any of these.
- **Never write it.** No identifying value may appear in a schema, crosswalk,
  changelog, description, test result, report, commit message or chat reply.
  Variable *names* are fine; their *values* are not.
- **Never load it.** Direct identifiers are excluded from the warehouse with a
  written reason. Quasi-identifiers (date of birth, village, rare conditions,
  small facilities) are coarsened as an expression (date of birth to age or
  birth year, village to district) or excluded. Patient keys stay only when
  they are pseudonymous codes; mark them as identifiers so their values are
  never shown, and never use them to link to outside records.
- **Never list it.** Do not run the values tool, an EDA with labels, or any
  query that returns values on an identifier, a quasi-identifier or a
  free-text variable. Counts are allowed; values are not.
- **When in doubt, it is identifying.** Treat any variable whose catalogue
  entry, name or EDA shape (many distinct values, free text, dates of birth)
  suggests a person could be recognised as an identifier, exclude it, and ask
  the user before including it.
- **If it leaks, stop.** If a tool output, error message or file ever shows an
  identifying value, do not repeat it or act on it. Stop, tell the user which
  variable and which step exposed it (not the value), and fix the schema or
  check so it cannot happen again before going on.

### Rule 3 — Three inputs, and two are required
Every dataset arrives with:

| Input | Required | What it gives the agent |
| :--- | :--- | :--- |
| **Catalogue** (data dictionary) | yes | Variable names, meanings, types, codes. |
| **EDA** | yes | Aggregate shape of the real file: types, null rates, ranges, category counts. |
| **Description** (the template) | strongly expected | The study, the main question, what one row is, the main event, code lists, privacy constraints, instructions. |

All three live in one inputs folder per dataset, with the data and the code
lists (vocabularies). A dataset often arrives as several exports with several
dictionaries, so catalogues, data, EDAs and code lists are each a folder of
files. Catalogues and EDAs are merged by variable name, and every variable
records which data files carry it. Each data file loads as its own source of
the dataset. Paths always point inside that folder.

Without a catalogue and an EDA, stop and ask for them. Without a description,
the main fact and the grain are guesses — say so, and ask the user to fill the
template. Its questions exist because each answer drives a modelling decision.

The user answers only what they know about the study. The **visit**,
**entities** and **relationship to existing schemas** sections are the agent's
work: it models them from the catalogue, the EDA and the registry match, and
writes them into the description before attaching.

### Rule 4 — Zero variable loss
Every variable in the dataset must end up somewhere: a dimension attribute, a
fact measure or attribute, a key — or explicitly excluded with a written reason
(direct identifiers such as names and phone numbers are excluded, not dropped
silently). The load refuses to run while any variable is unaccounted for.

### Rule 5 — The visit is the spine
In medicine, nothing happens on its own. A diagnosis, a drug, a lab result, a
viral load — each happens **during a visit**. The visit dimension is the centre
of the model and every fact joins back to it.

```
                      +-------------------+
                      |    dim_patient    |
                      +---------+---------+
                                |
                      +---------v---------+
                      |     dim_visit     |
                      |   (Visit Spine)   |
                      +----+----+----+----+
                           |    |    |
           +---------------+    |    +----------------+
           |                    |                     |
+----------v---------+ +--------v--------+ +----------v---------+
|  fact_<event A>    | |  fact_<event B> | |  fact_<event C>    |
+--------------------+ +-----------------+ +--------------------+
```

If a dataset has no visit identifier, derive one (for example patient plus date)
as an expression and record how in the schema. If a fact row has no visit, the
model is wrong. Fix the model, not the data.

### Rule 6 — Reuse, never rename
Schemas are stored once and reused. A table name means the same table across
the whole warehouse: if `dim_patient` exists, a new dataset uses it. Changes are
**additive only** — new tables, new columns, new aliases, new links, new allowed
codes, new exclusions. Nothing existing is renamed, retyped or removed, because
data already loaded depends on it. A source column whose name differs from the
schema's (`sex` vs `gender`) is mapped with an alias, not a rename.

### Rule 7 — Keep the site's word, add the standard one
Sites name the same thing differently: one hospital's "Paediatrics" is
another's "Pediatric", "PAEDIATRIC" or "Children's Ward", and some wards exist
at one site only ("Burns Unit", "TB Ward"). The same name can even mean
different things at different sites ("Ward 4B"). Never overwrite the site's
label and never drop it: keep it exactly as recorded, and add a **standard
category** column mapped from it through a **crosswalk**. Analysis slices by
the standard category; the site's label stays for anyone who needs the detail.

Mapping labels is the agent's own judgement — the AI step in the middle of the
workflow. The tools list a vocabulary's distinct labels with their counts and
the sites they appear at (vocabularies only — never identifiers or free text);
the agent decides what each label means and writes the crosswalk. A label
whose meaning differs between sites gets one entry per site.

### Rule 8 — The numbers must still add up
After the data is split into tables, totals must match the source. Counts,
sums and entity counts in the warehouse must equal the same question asked of
the staged source. If they don't, the split was wrong.

---

## 2. One Constellation

There is one warehouse, and it is a **fact constellation**: many fact tables
sharing the same dimensions, all anchored on the visit spine. Each schema is a
view onto part of it — the tables one kind of study needs — and schemas overlap
wherever they share tables.

Each schema lives in one file. The file records which datasets are attached to
it, the main fact each dataset answers to, and, on every column, which datasets
feed it. That is how the output highlights where a dataset sits inside the
constellation.

---

## 3. How the Agent Works Through a Dataset

```mermaid
graph TD
    A[1. Collect description, catalogue, EDA] --> B[2. Understand the variables]
    B --> C[3. Match against the registry]
    C -- fit --> F[5. Attach the dataset]
    C -- extend --> D[4a. Write an additive patch]
    C -- new_schema --> E[4b. Model a new schema, reusing tables]
    D --> F
    E --> F
    F --> G[6. Dummy data: ETL, build, TEST.md]
    G -- a check failed --> H[Fix the schema] --> G
    G -- all pass --> R[Request the production data]
    R --> I[7. Production data: ETL, build, TEST.md]
    I -- a gate or check failed --> J[Debug: fix the schema or report a data issue] --> I
    I -- all pass --> K[8. Report back with the highlighted constellation]
```

### Step 1 — Collect the inputs
The agent runs this step for the user. Ask them to put the catalogue (and the
data or an EDA, and any code lists) in the dataset's inputs folder, and to
answer four questions: the study, the main question, what one row is, and the
main event. Then write the description with the one-line `describe(...)`
helper. It finds the files in the folder, makes the EDA from the data when
there is none (without showing the agent any rows), lists the code lists with
their sizes only, and writes the template's user sections. If the user would
rather do it themselves, give them the same one line to fill in. Any required
answer left empty is reported back; ask for it before modelling.

Code lists can be large (a full ICD-10 list). The agent works from their names
and sizes; it does not read them into context.

### Step 2 — Understand the variables
From the catalogue and the description, decide for each variable what it is:

- Does it **identify** something (visit, patient, facility, regimen, organism)? → a key.
- Does it **describe a person, place or thing** and stay the same for that thing? → a dimension attribute.
- Does it **record an event or a measurement**? → a fact measure or attribute.
- Does it take **several values within one visit** (several diagnoses, drugs, tests)? → its own fact table, with a grain that says what one row is.

The description's *main event* is the main fact. Use the EDA to confirm: null
rates show which variables travel together, distinct counts show which columns
can be keys, ranges show what a measure looks like.

Look for **vocabulary variables** whose labels vary by site — wards, units,
departments, specimen names, local test names. The EDA hides them when they
have many labels, so list them with the values tool (Rule 7). Each one becomes
two columns: the site's label (an existing column, matched by alias) and a
standard category with a crosswalk. Map every label. When unsure what a
site-specific label means, ask the user rather than guess.

Then fill the agent's sections of the description:

- **visit** — the variable that identifies a visit, or how to derive one
  (Rule 5), what one visit means here, and its date.
- **entities** — the patient, the facility and any other thing the rows
  describe, each with its identifier and the attributes that stay stable for it.

With several data files, check that every file carries the visit identifier
(or what it is derived from) — a file without it cannot join the spine. Files
can load in any order: a visit first seen in a facts file gets its details when
the visits file loads.

### Step 3 — Match against the registry
Score the dataset against every registered schema. The result is one of:

| Decision | Meaning | What the agent does |
| :--- | :--- | :--- |
| `fit` | Every variable already has a home. | Attach the dataset. |
| `extend` | Enough overlap (less than the configured difference, 80% by default). | Write an additive patch for the unmatched variables. |
| `new_schema` | Too different. | Model a new schema — reusing every existing table that applies. |

The match also reports **blockers** (an unresolved spine key, type conflicts,
required columns missing), **code gaps** (catalogue codes the schema does not
allow) and, for unmatched variables, **tables in other schemas that already
model them**. Resolve blockers in the patch. Reuse those tables. When two
schemas tie, choose the one whose main facts answer questions closest to the
description's main question — never by list order.

Record the outcome in the description's **relationship_to_existing**: the
schema, the decision, the datasets already attached to it that this one shares
tables with, and the keys it shares. Attaching is refused until the visit
identifier, schema and decision are filled.

### Step 4 — Extend or model
**Extending:** list what to add — tables, columns, aliases, links, codes,
exclusions — and why. The reason is written to the schema's changelog.

**Modelling new:** start from the existing shared tables (visit spine, patient,
facility) and add only what this study needs. Pick the main fact from the
description. Give every fact a grain. Exclude direct identifiers and coarsen
or exclude quasi-identifiers (Rule 2).

In both cases, write each variable's rules into the schema: type, allowed codes,
valid range, aliases, an expression where a value must be derived, and a
crosswalk where a site label maps to a standard category. Crosswalks grow
additively too: add entries freely, but change an existing mapping only with
the user's agreement.

### Step 5 — Attach the dataset
Attaching records the dataset on the schema (its catalogue, EDA and description,
and its own main fact) and tags every column it feeds.

### Step 6 — Prove it on dummy data
1. **ETL.** Generate dummy data from the schema (capped at 400 rows; raise the
   cap only when a check genuinely needs volume) and run the ETL with its gates
   and post-load checks.
2. **Build.** Run the warehouse pipeline on the loaded source: validation, join
   paths, slices and the reviewer page.
3. **Test.** Spawn the test sub-agents in [TEST.md](TEST.md) on the dummy
   warehouse.

Because dummy data obeys the schema by construction, any failure here is a
modelling or ETL defect, not a data problem. Fix the schema and repeat the three
steps until everything passes. Keep the dummy warehouse separate from the
production one.

Only then **ask the user for the production data**: confirm its file is in the
inputs folder and that it is the export the catalogue and EDA describe.

### Step 7 — Run the production data
Run the same three steps (ETL, build, TEST.md) on the production file, in its
own warehouse. The gates run first and refuse to load when something is wrong.
Debug from the results:

- **The schema is wrong** (a variable belongs on the visit, not the patient; a
  grain is too coarse) → extend the schema, prove the change on dummy data
  again (Step 6), then rerun production.
- **The data is wrong** (a patient recorded with two genders; codes outside the
  vocabulary) → report it to the user with the counts and ask how to proceed.
  Do not bend the schema to hide a data-quality problem, and do not force the
  load without the user's say-so.

Repeat until the ETL loads, the build completes and every TEST.md mandate
passes, or until only data issues the user has to decide on remain.

### Step 8 — Report back when it is done
Report once, at the end, with:

- the constellation view with this dataset's tables and variables highlighted,
  and the main fact it answers to;
- the check and TEST.md summary for dummy and production runs (counts only);
- every schema change made while debugging, from the changelog;
- any data issues found, with their counts, and what the user decided.

---

## 4. Traps to Watch For

- **Fan-out.** Flat exports repeat visit-level values on every row of a visit.
  Facts are de-duplicated to their grain; a measure summed on raw rows will be
  inflated.
- **Attributes that are not stable.** A value that changes between visits (age
  band, substance use) belongs on the visit or a fact, not on the patient. The
  entity-consistency gate catches this.
- **Silent merges across sources.** Two sites may reuse the same ids. When keys
  are shared with earlier sources, the load reports it — confirm they really are
  the same entities.
- **Labels that drift.** A new export brings ward names the crosswalk has
  never seen. The load stops and names them; map them and reload. Do not map
  a label you cannot interpret to `Other` to get past the gate.
- **Sneaky nulls.** A null key turns into a lost row at join time. Catch nulls
  at the gate, not after the build.
- **Float drift.** Compare sums and averages with a small tolerance.
- **Small cells.** Even aggregates can identify a person when a category is
  rare. The EDA hides small categories; do not try to recover them.

---

## 5. When You Are Ready to Run

Everything practical — the commands for each step, the schema and patch file
formats, the template, and the test suite — is in
**[HOW_TO_RUN.md](HOW_TO_RUN.md)**.
