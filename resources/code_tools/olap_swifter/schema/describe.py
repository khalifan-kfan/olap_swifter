"""
Dataset concept in one line.
Every dataset's inputs live in one folder, `inputs/<name>/` in the results
workspace; a dataset often comes as several files, so each kind of input is a
folder:

    catalogue/            (required) data dictionaries: .csv .yaml .yml .xlsx .xls, or tables in .docx .pdf
    data/                 raw exports: .csv .xlsx .xls (each sheet is a data file); only the code tools open them
    eda/                  one EDA per data file: .yaml .yml .json .csv .xlsx .xls; made from data/ if absent
    codes/                code lists / vocabularies, one file each: .csv .yaml .yml .txt .xlsx .xls .docx .pdf
    documents/            source documents (protocol, SOP): .pdf .docx .txt .md; read only for the concept's answers
    dataset_concept.yaml  written here: what the dataset is, and how the skill modelled it

A document longer than three pages is extracted to text first (in the
dataset's outputs folder, `documents_text/`), and the concept links each of
its template fields to the pages most likely to answer it, so the agent reads
those pages only.

`describe` writes the user's part of the dataset concept (the shape of
resources/templates/dataset_concept.yaml) from four answers:

    python -c "from olap_swifter import describe; describe('hiv_site_b', study='Adults on ART at 4 clinics, 2023-2025.', main_question='What share of patients are virally suppressed?', one_row_is='one viral load test', main_event='viral load test')"

The visit, entities and relationship_to_existing sections are left empty: the
skill models them from the catalogue, the EDA and the registry match. Returns
paths, counts and the fields still empty; never data rows or code values.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from olap_swifter.paths import route
from olap_swifter.schema.profile import (
    CATALOGUE_SUFFIXES, DESCRIPTION_REQUIRED, EDA_SUFFIXES, SKILL_REQUIRED, empty_fields, files_in, load_eda, profile_csv,
)
from olap_swifter.schema.readers import ACCEPTED, count_entries, data_entries, entry_stem, extract_document, unsupported
from olap_swifter.schema.registry import SchemaRegistry, default_inputs_dir
from olap_swifter.schema.spec import normalize_name

CATALOGUE_DIR = "catalogue"
DATA_DIR = "data"
EDA_DIR = "eda"
CODES_DIR = "codes"
DOCUMENTS_DIR = "documents"
DESCRIPTION_NAME = "dataset_concept.yaml"  # the dataset concept


def blank_description() -> Dict[str, Any]:
    """The template's structure with every field empty."""
    return {
        "dataset": {"name": "", "owner": "", "period_covered": "", "catalogue": "", "eda": "", "data_files": []},
        "study": {"title": "", "description": "", "study_type": "", "main_question": "",
                  "secondary_questions": []},
        "unit_of_analysis": {"one_row_is": "", "main_event": "", "main_measures": [],
                             "repeated_within_visit": []},
        "codes_and_vocabularies": [],
        "documents": [],
        "privacy": {"direct_identifiers": [], "quasi_identifiers": [], "eda_category_labels_allowed": True,
                    "other_language_vocabulary": []},
        "known_issues": "",
        "instructions_for_modeller": "",
        "placement": "",
        # filled by the skill
        "visit": {"identifier": "", "definition": "", "date_variable": ""},
        "entities": {"patient_identifier": "", "facility_identifier": "", "others": []},
        "relationship_to_existing": {"schema": "", "decision": "", "related_datasets": [], "shared_keys": []},
    }


# flat keyword -> (section, field); field None means the section itself
USER_FIELDS = {
    "study": ("study", "description"),
    "main_question": ("study", "main_question"),
    "one_row_is": ("unit_of_analysis", "one_row_is"),
    "main_event": ("unit_of_analysis", "main_event"),
    "owner": ("dataset", "owner"),
    "period_covered": ("dataset", "period_covered"),
    "title": ("study", "title"),
    "study_type": ("study", "study_type"),
    "secondary_questions": ("study", "secondary_questions"),
    "main_measures": ("unit_of_analysis", "main_measures"),
    "repeated_within_visit": ("unit_of_analysis", "repeated_within_visit"),
    "direct_identifiers": ("privacy", "direct_identifiers"),
    "quasi_identifiers": ("privacy", "quasi_identifiers"),
    "eda_labels_allowed": ("privacy", "eda_category_labels_allowed"),
    "other_languages": ("privacy", "other_language_vocabulary"),
    "known_issues": ("known_issues", None),
    "instructions": ("instructions_for_modeller", None),
    "placement": ("placement", None),
}


def _inside(folder: Path, name: str, what: str) -> Path:
    """Resolves `name` inside the dataset's inputs folder; anything outside is refused."""
    path = (folder / name).resolve()
    if folder.resolve() not in path.parents:
        raise ValueError(f"{what} must be inside {folder}: {name!r}")
    if not path.exists():
        raise FileNotFoundError(f"{what} not found: {path}")
    return path


def _code_entries(folder: Path, codes: Union[None, Dict[str, str], List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    """Code-list entries: the ones named, plus every other file found in codes/ (variable = file stem)."""
    if isinstance(codes, dict):
        codes = [{"variable": v, "file": f} for v, f in codes.items()]
    entries, used = [], set()
    for e in codes or []:
        f = str(e["file"])
        rel = f if f.startswith(f"{CODES_DIR}/") else f"{CODES_DIR}/{f}"
        path = _inside(folder, rel, f"code list for {e.get('variable')}")
        used.add(path)
        entries.append({"variable": e.get("variable", path.stem), "system": e.get("system", ""), "file": rel})
    codes_dir = folder / CODES_DIR
    if codes_dir.is_dir():
        for path in sorted(p for p in codes_dir.iterdir() if p.is_file() and not p.name.startswith(".")
                           and p.suffix.lower() in ACCEPTED["codes"]):
            if path.resolve() not in used:
                entries.append({"variable": path.stem, "system": "", "file": f"{CODES_DIR}/{path.name}"})
    for e in entries:  # size only, never the codes themselves
        e["entries"] = count_entries(folder / e["file"])
    return entries


def _documents(folder: Path, name: str) -> List[Dict[str, Any]]:
    """Each source document's size; long ones extracted to text and linked to the concept's fields."""
    docs_dir = folder / DOCUMENTS_DIR
    if not docs_dir.is_dir():
        return []
    out_dir = route("dataset_outputs") / name / "documents_text"
    out = []
    for p in files_in(docs_dir, ACCEPTED["documents"]):
        info = extract_document(p, out_dir)
        info["file"] = f"{DOCUMENTS_DIR}/{p.name}"
        out.append(info)
    return out


def _eda_for(data_file: str, eda_dir: Path, cfg: Dict[str, Any]) -> (Path, bool):
    """
    The EDA of one data file (or Excel sheet): an existing eda/<stem> in any
    accepted EDA format (the user's own, or a ydata-profiling JSON report),
    else eda/<stem>.yaml made here (aggregates only).
    """
    existing = [eda_dir / f"{entry_stem(Path(data_file).name)}{suffix}" for suffix in EDA_SUFFIXES]
    for path in existing:
        if path.exists():
            return path, False
    profile = profile_csv(data_file, max_categories=int(cfg["eda_max_categories"]),
                          min_category_count=int(cfg["eda_min_category_count"]))
    eda_dir.mkdir(exist_ok=True)
    existing[0].write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return existing[0], True


def describe(name: str, study: str = "", main_question: str = "", one_row_is: str = "", main_event: str = "",
             codes: Union[None, Dict[str, str], List[Dict[str, str]]] = None,
             inputs_dir: Optional[str] = None, registry: Optional[SchemaRegistry] = None,
             quiet: bool = False, **optional: Any) -> Dict[str, Any]:
    """
    Writes `<inputs>/<name>/dataset_concept.yaml`. Reads every dictionary in
    catalogue/ and every data file in data/ (each Excel sheet is one); each
    data file without an EDA in eda/ gets one (aggregates only, small cells
    hidden). Documents longer than three pages are extracted to text and
    linked to the template fields they answer. `codes` maps variables to
    files in codes/, e.g. {'diagnosis_code': 'icd10.csv'}. Re-running keeps
    anything the skill already filled in.
    """
    if optional.get("placement") and str(optional["placement"]).lower() not in ("extend", "own_mart", "ask_me"):
        raise ValueError("placement must be 'extend', 'own_mart' or 'ask_me'")
    unknown = sorted(set(optional) - set(USER_FIELDS))
    if unknown:
        raise TypeError(f"unknown description field(s): {unknown}; use one of {sorted(USER_FIELDS)}")
    folder = Path(inputs_dir or default_inputs_dir()) / name
    if not folder.is_dir():
        raise FileNotFoundError(f"inputs folder not found: {folder} (put catalogue/ and data/ there)")

    cat_dir, data_dir, eda_dir = folder / CATALOGUE_DIR, folder / DATA_DIR, folder / EDA_DIR
    catalogues = files_in(cat_dir, CATALOGUE_SUFFIXES) if cat_dir.is_dir() else []
    if not catalogues:
        raise FileNotFoundError(f"no catalogue in {cat_dir} (one or more {' '.join(CATALOGUE_SUFFIXES)} files)")
    data_files = data_entries(data_dir)

    cfg = (registry or SchemaRegistry()).config()
    files, generated = [], 0
    for entry in data_files:
        eda_path, made = _eda_for(str(data_dir / entry), eda_dir, cfg)
        generated += made
        eda = load_eda(str(eda_path))
        files.append({"data": f"{DATA_DIR}/{entry}", "eda": f"{EDA_DIR}/{eda_path.name}",
                      "rows": eda.get("row_count"), "variables": eda.get("column_count")})
    edas = files_in(eda_dir, EDA_SUFFIXES) if eda_dir.is_dir() else []
    if not edas:
        raise ValueError(f"an EDA is required: put CSVs in {data_dir} or EDA files in {eda_dir}")

    out_path = folder / DESCRIPTION_NAME
    doc = blank_description()
    if out_path.exists():  # keep the skill's sections and earlier answers
        previous = yaml.safe_load(out_path.read_text(encoding="utf-8")) or {}
        for section, value in previous.items():
            if isinstance(value, dict) and isinstance(doc.get(section), dict):
                doc[section].update(value)
            elif section in doc:
                doc[section] = value

    doc["dataset"].update({"name": name, "catalogue": f"{CATALOGUE_DIR}/", "eda": f"{EDA_DIR}/",
                           "data_files": [f["data"] for f in files]})
    doc["dataset"].pop("data_file", None)
    answers = {"study": study, "main_question": main_question, "one_row_is": one_row_is, "main_event": main_event}
    for key, value in {**{k: v for k, v in answers.items() if v}, **optional}.items():
        section, field = USER_FIELDS[key]
        if field is None:
            doc[section] = value
        else:
            doc[section][field] = value
    doc["codes_and_vocabularies"] = _code_entries(
        folder, codes if codes is not None else doc["codes_and_vocabularies"])
    doc["documents"] = _documents(folder, name)
    skipped = {kind: unsupported(folder / kind, kind) for kind in ACCEPTED}
    skipped = {k: v for k, v in skipped.items() if v}

    header = ("# Dataset concept (template: resources/templates/dataset_concept.yaml).\n"
              "# User part written by olap_swifter.describe; visit, entities and\n"
              "# relationship_to_existing are filled by the skill.\n")
    out_path.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")

    result = {"ok": True, "folder": str(folder), "dataset_concept": str(out_path),
              "catalogue": str(cat_dir), "catalogue_files": [c.name for c in catalogues],
              "eda": str(eda_dir), "eda_generated": generated, "data_files": files,
              "sources": [normalize_name(f"{name}_{entry_stem(Path(f['data']).name)}") for f in files],
              "code_lists": [{"variable": e["variable"], "file": e["file"], "entries": e["entries"]}
                             for e in doc["codes_and_vocabularies"]],
              "documents": [{k: d[k] for k in ("file", "pages", "words", "text", "concept_links") if k in d}
                            for d in doc["documents"]],
              "unsupported_files": skipped,
              "missing_required": empty_fields(doc, DESCRIPTION_REQUIRED),
              "skill_to_fill": empty_fields(doc, SKILL_REQUIRED)}
    if not quiet:
        status = "complete" if not result["missing_required"] else \
            f"missing: {', '.join(result['missing_required'])}"
        print(f"wrote {out_path} ({status}); {len(catalogues)} catalogue file(s), {len(files)} data file(s), "
              f"{generated} EDA(s) generated, {len(result['code_lists'])} code list(s), "
              f"{len(doc['documents'])} document(s)")
        for kind, names in skipped.items():
            print(f"  not read ({kind}/ accepts {' '.join(ACCEPTED[kind])}): {', '.join(names)}")
    return result
