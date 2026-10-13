"""
Schema art: a plain SVG of the constellation for the review page. Dimensions on
top, the visit spine in the middle, facts below; the dataset's tables are filled.
"""

from html import escape
from typing import Any, Dict

W, H, GAP = 150, 30, 20


def render_svg(view: Dict[str, Any]) -> str:
    spines = [t for t in view["tables"] if t["name"] in view["spines"]]
    dims = [t for t in view["tables"] if t["kind"] == "dimension" and t["name"] not in view["spines"]]
    facts = [t for t in view["tables"] if t["kind"] == "fact"]
    rows = [(dims, 20), (spines, 120), (facts, 220)]
    width = max(len(r) for r, _ in rows) * (W + GAP) + GAP
    pos = {}
    for tables, y in rows:
        x0 = (width - len(tables) * (W + GAP) + GAP) / 2
        for i, t in enumerate(tables):
            pos[t["name"]] = (x0 + i * (W + GAP), y)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} 270" class="schema-art" '
             f'role="img" aria-label="Warehouse constellation">']
    for r in view["relationships"]:
        if r["from"] in pos and r["to"] in pos:
            (x1, y1), (x2, y2) = pos[r["from"]], pos[r["to"]]
            parts.append(f'<line x1="{x1 + W / 2:.0f}" y1="{y1 + H / 2:.0f}" x2="{x2 + W / 2:.0f}" '
                         f'y2="{y2 + H / 2:.0f}" class="edge"/>')
    for t in view["tables"]:
        x, y = pos[t["name"]]
        cls = ("hl " if t["highlighted"] else "") + ("fact" if t["kind"] == "fact" else "dim")
        rx = 15 if t["kind"] == "fact" else 4
        parts.append(f'<g class="{cls}"><title>{escape(t["name"])}: {len(t["columns"])} columns</title>'
                     f'<rect x="{x:.0f}" y="{y}" width="{W}" height="{H}" rx="{rx}"/>'
                     f'<text x="{x + W / 2:.0f}" y="{y + 19}">{escape(t["name"])}</text></g>')
    parts.append("</svg>")
    return "".join(parts)
