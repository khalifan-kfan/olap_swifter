# Packages

Every third-party package the code tools use, where it is used, and its last
security audit. `pyproject.toml` declares them; the `Dockerfile` installs them
for the container. Update this file whenever either changes.

Last audit: **2026-10-15**, `pip-audit` on `.venv` (Python 3.11): **no known
vulnerabilities** after the upgrades noted below.

## Runtime

| Package | Floor (pyproject) | Installed | Used by | Notes |
| :--- | :--- | :--- | :--- | :--- |
| duckdb | >=0.10.0 | 1.5.6 | `olap_swifter` (builder, olap, profiler, schema, schema_cli) | Warehouse engine |
| pandas | >=2.0.0 | 3.0.6 | `olap_swifter` (builder, olap, profiler) | |
| networkx | >=3.1 | 3.6.1 | `olap_swifter/profiler` | Join paths |
| PyYAML | >=6.0 | 6.0.3 | `olap_swifter` (paths, schema, schema_cli) | Always `yaml.safe_load`; never `yaml.load` |
| typer | >=0.9.0 | 0.27.3 | `olap_swifter` (cli, schema_cli) | CLI |
| rich | >=13.7.0 | 15.0.0 | `olap_swifter` (cli, schema_cli) | CLI output |
| playwright | >=1.40.0 | 1.63.0 | `olap_swifter/feedback` | Review page screenshots. Not in the Dockerfile |
| pydantic | >=2.5.0 | 2.13.5 | — | Declared but not imported. Candidate to remove |
| pytesseract | >=0.3.10 | 0.3.13 | `ocr_tools` | Needs the `tesseract` binary |
| Pillow | >=10.0.0 | 12.3.0 | `ocr_tools` | Opens untrusted scans; keep current |
| presidio-analyzer | >=2.2.354 | 2.2.364 | `privacy_tools` | De-identification |
| presidio-anonymizer | >=2.2.354 | 2.2.364 | `privacy_tools` | Pins `cryptography<49` (see below) |
| spacy | — (Dockerfile only) | 3.8.16 | `privacy_tools` | Plus the `en_core_web_sm` model. Not in pyproject |

## Transitive packages to watch

| Package | Installed | Why |
| :--- | :--- | :--- |
| cryptography | 50.0.2 | Pulled in by presidio-anonymizer. 48.0.1 had PYSEC-2026-3552/3553/3554. Upgraded beyond presidio's `<49` pin, so `pip check` reports a conflict. The repo never uses presidio's encrypt operator, and that operator still works on 50.0.2. Go back to presidio's pin once a presidio release allows >=50. |
| pip | 26.2.1 | 23.1.2 had PYSEC-2023-228 and PYSEC-2026-196/1795/1796/2875/2876/3721 |
| setuptools | 84.0.0 | 65.5.0 had PYSEC-2022-43012, PYSEC-2025-49, PYSEC-2026-1918/3447 |

## Optional, not installed

| Package | Used by | Notes |
| :--- | :--- | :--- |
| ydata-profiling | the user, to make an EDA in one line (HOW_TO_RUN.md, Step 1) | Not a dependency: 4.18.4 needs pandas < 3, and this repo runs pandas 3. Install it in its own environment. The tools only read its JSON report (`schema/profile.py`), so nothing here imports it. |

## Dev

| Package | Floor | Installed | Notes |
| :--- | :--- | :--- | :--- |
| pytest | >=7.4.0 | 9.1.1 | |
| pytest-asyncio | >=0.21.0 | not installed | No async tests use it. Candidate to remove |

## Re-audit

Run pip-audit from its own venv, not the project's:

```bash
python3 -m venv /tmp/auditenv && /tmp/auditenv/bin/pip install pip-audit
.venv/bin/python -m pip list --format=freeze | grep -v '^olap-swifter' > /tmp/freeze.txt
/tmp/auditenv/bin/pip-audit -r /tmp/freeze.txt --no-deps --disable-pip
.venv/bin/python -m pip check
```

The Dockerfile installs packages unpinned at build time, so a rebuilt image
can differ from this list. Audit the image too after a rebuild.
