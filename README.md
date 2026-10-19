# OLAP Swifter (`olap_swifter`)
``` Public on github at https://github.com/khalifan-kfan/olap_swifter ```
> An Antigravity (Gemini) skill that turns flat, messy clinical CSV files into a
> medical OLAP data warehouse  and then proves the warehouse still tells the
> truth.

---

## What it does

(Words like *fact*, *dimension*, *grain* and *crosswalk* are defined under
[TERMS.md](resources/instructions/TERMS.md).)

Clinical data usually arrives as one very wide spreadsheet: one row per record,
dozens of columns mixing patients, facilities, diagnoses, drugs and lab results
all together. That shape is fine for collecting data and useless for analysing
it.

This skill reads a file like that and splits it into a proper warehouse:

- **Dimensions** — the things being described (patients, facilities, drugs, tests).
- **Facts** — the things that happened or were measured (costs, stays, lab results).
- **The visit spine** — the central `dim_visit` table that ties every fact back
  to the encounter it happened in.

Then it checks its own work. Every column must survive the split, every fact row
must link to a visit, and every total must still match the original file.

---

## The four documents

| File | Read it when |
| :--- | :--- |
| **[SKILL.md](SKILL.md)** | You want the rules and the thinking — what a good model looks like and why. |
| **[HOW_TO_RUN.md](resources/instructions/HOW_TO_RUN.md)** | You want to actually run something: commands, SQL, code, tests. |
| **[TEST.md](TEST.md)** | You want the sub-agent test prompts and their pass conditions. |
| **[TERMS.md](resources/instructions/TERMS.md)** | A word is unfamiliar: crosswalk, grain, gate, patch, alias and the rest, in plain language. |

`SKILL.md` deliberately contains no commands. `HOW_TO_RUN.md` deliberately
contains all of them. [`resources/ROUTES.yaml`](resources/ROUTES.yaml) says where
every file lives.

---

## How to run the skill in Gemini (Antigravity)

A skill is just a folder with a `SKILL.md` in it. Antigravity finds it, reads
the description, and switches it on when your request matches.

### Step 1 — Put the skill where Antigravity looks

**For this project only** (recommended — it travels with the repo):

```bash
cd /usr/olap_swifter
mkdir -p .agents/skills/medical-olap-orchestrator
cp SKILL.md TEST.md resources/instructions/HOW_TO_RUN.md resources/instructions/TERMS.md .agents/skills/medical-olap-orchestrator/
```

**For every project on this machine:**

```bash
mkdir -p ~/.gemini/config/skills/medical-olap-orchestrator
cp SKILL.md TEST.md resources/instructions/HOW_TO_RUN.md resources/instructions/TERMS.md ~/.gemini/config/skills/medical-olap-orchestrator/
```

The folder must be named `skills/<skill-name>/` and must contain `SKILL.md`.
Antigravity walks up from your working directory to the repository root looking
for `.agents/`, then falls back to `~/.gemini/config/`.

### Step 2 — Open the project in Antigravity

Open this folder as your workspace. The skill is picked up automatically —
there is nothing to install or register.

### Step 3 — Ask for what you want

The skill switches itself on when your request matches its description. Plain
language is enough:

```
I have a new dataset to build a medical OLAP warehouse from
```

Before anything else, the skill creates your **results workspace**
(`results_workspace/`, in the folder you are working in, outside the skill),
asks you for everything in the **dataset concept** template (the study, the
main question, what one row is, the files, privacy) in one message, and tells
you where to put the files.

It never reads your data rows: it works from the catalogue and an **EDA
(Exploratory Data Analysis)**, a summary of the data's shape (column types,
missing values, ranges, category counts) with no rows in it. Give one, make one
with a ydata-profiling one-liner (see HOW_TO_RUN.md), or let the tools make it
from the data.

When it is done it hands you the workspace: its `README.md` is the results key
(a link to the review page `review/model_reviewer.html`, the folder structure,
and the decisions made for each dataset). In a chat, it offers the workspace as
a zip to download.

```
Model this clinical CSV as a fact constellation and prove no column was lost
```

```
Check the warehouse totals still match the raw file
```

You can also name it directly if it does not activate on its own:

```
Use the medical-olap-orchestrator skill on resources/datasets/sample_wide_clinical.csv
```

### Step 4 — Let it work through the five steps

The agent will read the CSV, plan the tables, run its pre-flight checks, build
the warehouse in DuckDB, send sub-agents to test it, and fix anything that
fails. You will see each step reported as it goes.

### If the skill does not activate

- Confirm the file is at `.agents/skills/medical-olap-orchestrator/SKILL.md`.
- Confirm `SKILL.md` still has its `name:` and `description:` frontmatter at the
  very top — that description is what Antigravity matches against.
- Name the skill directly in your prompt.

---

## Running it without an agent

You do not need Gemini at all. The same warehouse can be built by hand:

```bash
# Build from a dataset
.venv/bin/python -m olap_swifter.cli build --csv resources/datasets/sample_amr_clinical.csv

# Profile join paths, cube slices and query latency
.venv/bin/python -m olap_swifter.cli profile

# Open the interactive review page
.venv/bin/python -m olap_swifter.cli review

# Run the whole test suite
.venv/bin/pytest -v
```

Full details, including the raw DuckDB SQL, are in
[**HOW_TO_RUN.md**](resources/instructions/HOW_TO_RUN.md).

**Browse a warehouse (people only, shows real rows):** `.venv/bin/python -m olap_swifter.cli view`
opens http://127.0.0.1:8765, read-only, listing every workspace `.duckdb`, its tables and the first 200 rows (`--db`, `--limit`, `--port`).

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e resources/code_tools
```

---

## What is in the box

```
olap_swifter/
├── SKILL.md                  # The rules and the thinking
├── TEST.md                   # Sub-agent test definitions
├── README.md                 # This file
├── docker-compose.yml        # Run the code tools in a container
├── resources/
│   ├── ROUTES.yaml           # Routing key: where every resource lives, by name
│   ├── instructions/         # HOW_TO_RUN.md, TERMS.md
│   ├── config.yaml           # Generic thresholds (match, EDA privacy, dummy cap)
│   ├── templates/            # Dataset concept template
│   ├── datasets/             # Sample CSVs for the tool tests (git-ignored)
│   └── code_tools/
│       ├── olap_swifter/     # Warehouse: schema, ETL, checks, cube, reviewer, CLI, tests
│       └── privacy_tools/    # Presidio de-identification
└── results_workspace/        # Results workspace (git-ignored); README.md is its key
    ├── inputs/<name>/        # Your datasets: catalogue/, data/, eda/, codes/, documents/, dataset_concept.yaml
    ├── schemas/              # The live schema registry + crosswalks/, built from your datasets
    ├── warehouse/            # The output DuckDB files
    └── ...                   # dummy data, per-dataset outputs, process log, past runs
```

Nothing under `resources/` is about a particular dataset: it is the skill's
tools, instructions and generic settings. The results workspace is shown here
as it lands when you run the tools from inside this repo; run them from your
own project folder and it is created there instead. **Your datasets go in
`results_workspace/inputs/<name>/`**, and the schemas built from them live in
`results_workspace/schemas/`. None of it is ever committed. On a fresh clone the
registry starts empty, so the first dataset is modelled as a new schema.

---

## The guarantees

1. **Zero variable loss.** Every raw column lands in a dimension or a fact.
   Nothing is quietly dropped.
2. **A visit for every fact.** No orphan rows — every clinical event links back
   to the encounter it happened in.
3. **The numbers still add up.** Sums, averages, row counts and group
   distributions on the warehouse match the raw file within float tolerance.
4. **No fan-out inflation.** Joining fact tables through the visit spine does
   not multiply rows or overstate totals.

---

## Linked skill: medical OCR

Paper records are handled by a separate skill beside this repo,
[`../medical_ocr_skill`](../medical_ocr_skill/README.md). It turns scanned
clinical forms into de-identified rows, which come into this skill as an
ordinary dataset. It has its own `SKILL.md`, `TEST.md` and `HOW_TO_RUN.md`,
and uses this repo's `privacy_tools` for Presidio.

---

## TODO: AI Misuse Risks and Docker

### 1. Risks of AI Misuse & Clinical Governance
Automating OLAP aggregation with AI introduces high-stakes risks that require strict operational guardrails:
- **Misapplication to Real-Time Clinical Decision Support**:
  - `olap_swifter` is designed for **retrospective epidemiological research and health system reporting**, NOT real-time diagnostic triage or autonomous drug prescribing.
  - *Guardrail*: Outputs must never be fed directly into active bedside treatment algorithms without direct clinician review.
- **De-identification is English-only (Presidio)**:
  - Presidio's models are English. Text in another language can have ordinary local terms redacted as names, and real names missed.
  - *Guardrail*: flag variables and terms recorded in another language in the dataset concept (`privacy.other_language_vocabulary`) so they are kept as written and not anonymised, and have a person check names in that language.
- **Re-Identification of Vulnerable Populations**:
  - Even without direct names, high-dimensional OLAP slicing combining facility + rare diagnosis + exact visit date + age group can re-identify marginalized patients (e.g., HIV, MDR-TB, stigmatized conditions).
  - *Guardrail*: Enforce k-anonymity thresholds ($k \ge 5$) on cube rollups and suppress small cell counts before exporting reports.

### 2. Docker Compose Setup for the Skills (Next Iteration)
Testing the skill today depends on a local `.venv`, a system DuckDB, and the machine-specific paths in `HOW_TO_RUN.md`. That makes results hard to reproduce across contributors and CI. For the next iterations:
- **`docker-compose.yml` at the repo root**: One service that installs `resources/code_tools`, mounts the repo (inputs and `results_workspace/` included), and exposes the `olap_swifter.cli` entry point (`build`, `profile`, `review`).
- **One-command test run**: `docker compose run --rm olap pytest -v` should execute the full suite against a clean container, so sub-agents in `TEST.md` can be run without touching the host environment.
- **Pin the toolchain**: Lock Python, DuckDB and Presidio versions in the image.
- **Update `HOW_TO_RUN.md`**: Replace the absolute `/Users/...` paths with container paths and add a "Run with Docker" section alongside the `.venv` instructions.
