"""
Input File Readers.
One place for every file type a dataset's inputs may come in. Tables (data,
catalogues, EDAs, code lists) are read from CSV, Excel, YAML, Word or PDF;
source documents are read as text. Nothing here returns data rows to the
skill: data files are only converted for the code tools to stage.

Accepted files per input folder (ACCEPTED):

    data/        .csv .xlsx .xls            every sheet of a workbook is its own data file
    eda/         .yaml .yml .json .csv .xlsx .xls   one per data file (stem = the data file's)
    catalogue/   .csv .yaml .yml .xlsx .xls .docx .pdf   tables with a variable/name column
    codes/       .csv .yaml .yml .txt .xlsx .xls .docx .pdf
    documents/   .pdf .docx .txt .md        read as text; long ones are extracted first

An Excel data sheet is converted to a hidden CSV next to the workbook
(`data/.sheets/<stem>__<sheet>.csv`, all values as text), so staging and
profiling work on it exactly as on a CSV export. A data file name may carry a
sheet: `visits.xlsx#Sheet2`.
"""

import csv
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ACCEPTED = {
    "data": (".csv", ".xlsx", ".xls"),
    "eda": (".yaml", ".yml", ".json", ".csv", ".xlsx", ".xls"),
    "catalogue": (".csv", ".yaml", ".yml", ".xlsx", ".xls", ".docx", ".pdf"),
    "codes": (".csv", ".yaml", ".yml", ".txt", ".xlsx", ".xls", ".docx", ".pdf"),
    "documents": (".pdf", ".docx", ".txt", ".md"),
}
EXCEL = (".xlsx", ".xls")
SHEETS_DIR = ".sheets"
LONG_DOCUMENT_PAGES = 3   # longer documents are extracted to text before the agent reads them
WORDS_PER_PAGE = 500      # page estimate for documents without pages (docx, txt, md)


def unsupported(folder: Path, kind: str) -> List[str]:
    """Files in an input folder whose type is not accepted there."""
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() not in ACCEPTED[kind])


# ---------- tables ----------

def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def excel_sheets(path: Path) -> Dict[str, List[List[str]]]:
    """Every non-empty sheet of a workbook as rows of text cells (header first)."""
    path = Path(path)
    out: Dict[str, List[List[str]]] = {}
    if path.suffix.lower() == ".xls":
        import xlrd
        book = xlrd.open_workbook(str(path))
        for sh in book.sheets():
            rows = [[_cell(sh.cell_value(r, c)) for c in range(sh.ncols)] for r in range(sh.nrows)]
            rows = [r for r in rows if any(r)]
            if rows:
                out[sh.name] = rows
        return out
    import openpyxl
    book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        for sh in book.worksheets:
            rows = [[_cell(v) for v in r] for r in sh.iter_rows(values_only=True)]
            rows = [r for r in rows if any(r)]
            if rows:
                out[sh.title] = rows
    finally:
        book.close()
    return out


def _docx_tables(path: Path) -> List[List[List[str]]]:
    import docx
    return [[[c.text.strip() for c in row.cells] for row in t.rows] for t in docx.Document(str(path)).tables]


def _pdf_tables(path: Path) -> List[List[List[str]]]:
    import pdfplumber
    tables = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for t in page.extract_tables() or []:
                tables.append([[_cell(c) for c in row] for row in t if row])
    return tables


def _records(rows: List[List[str]]) -> List[Dict[str, str]]:
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:] if any(c for c in r)]


def table_records(path: Path, header_hint: Optional[Iterable[str]] = None) -> List[Dict[str, str]]:
    """
    Rows of a tabular file as dicts. CSV and Excel read every sheet; Word and
    PDF read every table. With `header_hint` (accepted header names, lower
    case), only tables whose header has one of them are kept, so a document's
    other tables are skipped.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    if suffix in EXCEL:
        tables = list(excel_sheets(path).values())
    elif suffix == ".docx":
        tables = _docx_tables(path)
    elif suffix == ".pdf":
        tables = _merge_pdf_continuations(_pdf_tables(path))
    elif suffix == ".txt":
        return [{"value": line.strip()} for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()]
    else:
        raise ValueError(f"not a table file: {path.name}")
    hint = {h.lower() for h in header_hint} if header_hint else None
    out: List[Dict[str, str]] = []
    for t in tables:
        if hint and not any(h.strip().lower() in hint for h in (t[0] if t else [])):
            continue
        out.extend(_records(t))
    return out


def _merge_pdf_continuations(tables: List[List[List[str]]]) -> List[List[List[str]]]:
    """A table that runs over a page break continues without its header: join it to the one before."""
    merged: List[List[List[str]]] = []
    for t in tables:
        if merged and t and len(t[0]) == len(merged[-1][0]) and t[0] != merged[-1][0] \
                and not any(re.search(r"[A-Za-z]{3,}", c or "") and c.lower() in
                            {h.lower() for h in merged[-1][0]} for c in t[0]):
            merged[-1].extend(t)
        else:
            merged.append(t)
    return merged


def count_entries(path: Path) -> int:
    """Entries in a code list (rows, never the codes)."""
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if isinstance(data, dict):
            data = data.get("codes") or data.get("entries") or data
        return len(data)
    return len(table_records(path))


# ---------- data files ----------

def split_sheet(name: str) -> Tuple[str, Optional[str]]:
    """`visits.xlsx#Sheet2` -> (`visits.xlsx`, `Sheet2`)."""
    base, _, sheet = str(name).partition("#")
    return base, (sheet or None)


def data_entries(folder: Path) -> List[str]:
    """Data files in a data/ folder, one entry per Excel sheet (`book.xlsx#Sheet`)."""
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in ACCEPTED["data"]:
            continue
        if p.suffix.lower() in EXCEL:
            sheets = list(excel_sheets(p))
            out.extend([p.name] if len(sheets) == 1 else [f"{p.name}#{s}" for s in sheets])
        else:
            out.append(p.name)
    return out


def entry_stem(entry: str) -> str:
    """The name a data file's EDA and source take: `visits`, or `book__Sheet2` for one sheet of several."""
    base, sheet = split_sheet(entry)
    stem = Path(base).stem
    return f"{stem}__{re.sub(r'[^A-Za-z0-9]+', '_', sheet).strip('_')}" if sheet else stem


def as_csv(path: str) -> str:
    """
    A path the CSV readers can open. A CSV is returned as is; an Excel sheet
    (`book.xlsx`, or `book.xlsx#Sheet`) is written once to data/.sheets/ as
    all-text CSV and that path returned. Rewritten when the workbook changes.
    """
    base, sheet = split_sheet(path)
    p = Path(base)
    if p.suffix.lower() not in EXCEL:
        return str(p)
    sheets = excel_sheets(p)
    if not sheets:
        raise ValueError(f"{p.name} has no non-empty sheet")
    if sheet is None:
        if len(sheets) > 1:
            raise ValueError(f"{p.name} has {len(sheets)} sheets ({', '.join(sheets)}); name one as {p.name}#<sheet>")
        sheet = next(iter(sheets))
    if sheet not in sheets:
        raise ValueError(f"{p.name} has no sheet {sheet!r}; sheets: {', '.join(sheets)}")
    out = p.parent / SHEETS_DIR / f"{entry_stem(f'{p.name}#{sheet}') if len(sheets) > 1 else p.stem}.csv"
    if not out.exists() or out.stat().st_mtime < p.stat().st_mtime:
        out.parent.mkdir(exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(sheets[sheet])
    return str(out)


# ---------- documents ----------

def document_pages(path: Path) -> List[str]:
    """A document's text, one string per page (Word and text files: ~500-word pages)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return [(page.extract_text() or "") for page in pdf.pages]
    if suffix == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            parts.extend(" | ".join(c.text.strip() for c in row.cells) for row in t.rows)
        text = "\n".join(parts)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    # ~500 words a page, split inside long paragraphs too, line breaks kept
    pages, page, count = [], [], 0
    for line in text.splitlines():
        words = line.split(" ")
        while True:
            room = WORDS_PER_PAGE - count
            n = len([w for w in words if w])
            if n <= room:
                page.append(" ".join(words))
                count += n
                break
            cut, seen = 0, 0
            while seen < room:
                seen += bool(words[cut])
                cut += 1
            page.append(" ".join(words[:cut]))
            pages.append("\n".join(page))
            page, count, words = [], 0, words[cut:]
        if count >= WORDS_PER_PAGE:
            pages.append("\n".join(page))
            page, count = [], 0
    if any(p.strip() for p in page) or not pages:
        pages.append("\n".join(page))
    return pages


# Where in a source document each dataset-concept answer usually is.
CONCEPT_KEYWORDS = {
    "study.description": ["background", "introduction", "population", "setting", "study design", "purpose"],
    "study.main_question": ["primary objective", "main objective", "primary outcome", "research question", "aim"],
    "study.secondary_questions": ["secondary objective", "secondary outcome", "specific objectives"],
    "study.study_type": ["cohort", "cross-sectional", "surveillance", "registry", "trial", "survey", "routine"],
    "unit_of_analysis.one_row_is": ["unit of analysis", "data collection", "case report form", "record", "each row"],
    "unit_of_analysis.main_event": ["visit", "encounter", "admission", "episode", "test", "specimen"],
    "codes_and_vocabularies": ["icd", "code list", "coding", "vocabulary", "loinc", "who stage", "regimen"],
    "privacy": ["confidential", "identifier", "anonymi", "de-identif", "consent", "privacy", "pseudonym"],
    "dataset.period_covered": ["study period", "between", "enrolment period", "data collection period"],
}


def concept_links(pages: List[str], max_pages: int = 3) -> Dict[str, List[int]]:
    """For each dataset-concept field, the pages (1-based) most likely to answer it."""
    links: Dict[str, List[int]] = {}
    lowered = [p.lower() for p in pages]
    for field, words in CONCEPT_KEYWORDS.items():
        scored = [(sum(p.count(w) for w in words), i + 1) for i, p in enumerate(lowered)]
        best = [i for score, i in sorted(scored, key=lambda s: (-s[0], s[1])) if score > 0][:max_pages]
        if best:
            links[field] = sorted(best)
    return links


def extract_document(path: Path, out_dir: Path) -> Dict[str, Any]:
    """
    Page and word counts of a source document. A document longer than
    LONG_DOCUMENT_PAGES pages is extracted to `<out_dir>/<name>.txt` with page
    markers, and linked to the dataset-concept fields its pages answer, so the
    agent reads only those pages of the text, never the whole document.
    """
    path = Path(path)
    pages = document_pages(path)
    info: Dict[str, Any] = {"file": path.name, "pages": len(pages),
                            "words": sum(len(p.split()) for p in pages)}
    if len(pages) > LONG_DOCUMENT_PAGES:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{path.stem}.txt"
        out.write_text("".join(f"\n\n===== page {i} =====\n{p}" for i, p in enumerate(pages, 1)).lstrip(),
                       encoding="utf-8")
        info["text"] = str(out)
        info["concept_links"] = concept_links(pages)
    return info
