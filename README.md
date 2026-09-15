# OLAP Swifter (`olap_swifter`)
``` Public on github at https://github.com/khalifan-kfan/olap_swifter ```
> An Antigravity (Gemini) skill that turns flat, messy clinical CSV files into a
> medical OLAP data warehouse  and then proves the warehouse still tells the
> truth.

---

## What it does

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

## The three documents

| File | Read it when |
| :--- | :--- |
| **[SKILL.md](SKILL.md)** | You want the rules and the thinking — what a good model looks like and why. |
| **[HOW_TO_RUN.md](HOW_TO_RUN.md)** | You want to actually run something: commands, SQL, code, tests. |
| **[TEST.md](TEST.md)** | You want the sub-agent test prompts and their pass conditions. |

`SKILL.md` deliberately contains no commands. `HOW_TO_RUN.md` deliberately
contains all of them.

---

## How to run the skill in Gemini (Antigravity)

A skill is just a folder with a `SKILL.md` in it. Antigravity finds it, reads
the description, and switches it on when your request matches.

### Step 1 — Put the skill where Antigravity looks

**For this project only** (recommended — it travels with the repo):

```bash
cd /Users/muwongekhalifan/Desktop/dexta_s_lab/olap_swifter
mkdir -p .agents/skills/medical-olap-orchestrator
cp SKILL.md HOW_TO_RUN.md TEST.md .agents/skills/medical-olap-orchestrator/
```

**For every project on this machine:**

```bash
mkdir -p ~/.gemini/config/skills/medical-olap-orchestrator
cp SKILL.md HOW_TO_RUN.md TEST.md ~/.gemini/config/skills/medical-olap-orchestrator/
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
Build a medical OLAP warehouse from resources/datasets/sample_amr_clinical.csv
```

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
[**HOW_TO_RUN.md**](HOW_TO_RUN.md).

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
├── SKILL.md                      # The rules and the thinking
├── HOW_TO_RUN.md                 # Every command and code example
├── TEST.md                       # Sub-agent test definitions
├── README.md                     # This file
├── model_reviewer.html           # Interactive review page
└── resources/
    ├── datasets/                 # All CSV datasets live here
    ├── images/                   # Sample scanned clinical forms
    └── code_tools/
        └── olap_swifter/         # builder, validator, cube, profiler, CLI, tests
```

**All datasets go in `resources/datasets/`.** Nowhere else.

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

## TODO: OCR Pipeline Implementation & AI Misuse Risks

### 1. Implementing the OCR Pipeline (`ocr/` & `privacy/`)
The repository contains scaffolding for `TesseractRunner`, `preprocessor.py`, and `OfflinePresidioAnonymizer`. To bring physical paper form digitization into production:
- **Image Preprocessing (`ocr/preprocessor.py`)**: Implement deskewing, contrast enhancement (CLAHE), adaptive binarization, and shadow removal to handle degraded paper records from clinic settings.
- **Structured Form Layout Parsing**: Replace raw text dump with zone/table detection (bounding box segmentation for Patient Header, Diagnosis blocks, and AST Lab sensitivity grids).
- **Automated Redaction & Privacy (`privacy/presidio_engine.py`)**: Gate raw OCR outputs through local Presidio de-identification to strip direct identifiers (patient names, phone numbers, village addresses) before staging.
- **Pipeline Integration**: Ingest sanitized OCR output directly into the DuckDB staging table (`raw_clinical_records`) to seamlessly trigger the dynamic constellation builder.

### 2. Risks of AI Misuse & Clinical Governance
Automating medical digitization and OLAP aggregation with AI introduces high-stakes risks that require strict operational guardrails:
- **Dosage & Metric Hallucinations (Silent Data Corruption)**:
  - OCR and generative models can easily misread or hallucinate handwritten decimals and dosages (e.g., misreading `0.5g` as `5g`, or flipping AST interpretation `S` to `R`). In surveillance warehouses, this produces dangerously distorted resistance curves.
  - *Guardrail*: Always enforce deterministic regex range checks (e.g., physiological vital boundaries, standard antimicrobial dosage tiers) and require human-in-the-loop (HITL) verification for low-confidence reads.
- **Misapplication to Real-Time Clinical Decision Support**:
  - `olap_swifter` is designed for **retrospective epidemiological research and health system reporting**, NOT real-time diagnostic triage or autonomous drug prescribing.
  - *Guardrail*: Outputs must never be fed directly into active bedside treatment algorithms without direct clinician review.
- **Re-Identification of Vulnerable Populations**:
  - Even without direct names, high-dimensional OLAP slicing combining facility + rare diagnosis + exact visit date + age group can re-identify marginalized patients (e.g., HIV, MDR-TB, stigmatized conditions).
  - *Guardrail*: Enforce k-anonymity thresholds ($k \ge 5$) on cube rollups and suppress small cell counts before exporting reports.
- **Adversarial Input & Prompt Injection**:
  - Scanned clinical notes containing malicious or unintended text instructions could compromise downstream LLM-based orchestrators.
  - *Guardrail*: Treat all OCR output strictly as raw data payloads; never pass raw scanned text directly as system prompt instructions.
