"""
Medical OLAP Orchestrator.
Runs the code-side pipeline for one registered schema: load a source (dummy or
real) through the schema ETL, then validate, profile joins, slice the cube and
render the reviewer. Every table name, join and dimension comes from the
schema YAML, so the pipeline is the same for AMR, HIV or any other study.

OCR (the separate ocr_tools package) is deliberately not wired in here yet; it will feed the
warehouse later as another data source.
"""

from typing import Any, Dict, List, Optional

from olap_swifter.builder import DynamicOLAPBuilder
from olap_swifter.feedback.worklog import render_review
from olap_swifter.olap.store import DEFAULT_SCHEMA, MedicalOLAPStore
from olap_swifter.profiler.join_graph import JoinGraph
from olap_swifter.profiler.latency import LatencyProfiler
from olap_swifter.profiler.slicer import CubeSlicer
from olap_swifter.schema.checks import run_checks
from olap_swifter.schema.materializer import resolve_source
from olap_swifter.schema.matcher import match_registry
from olap_swifter.schema.profile import merge_profile_and_dictionary, profile_csv
from olap_swifter.schema.spec import SchemaSpec


class MedicalOLAPOrchestrator:
    """Master orchestrator for schema-driven clinical OLAP warehousing."""

    def __init__(self, db_path: Optional[str] = None):
        self.store = MedicalOLAPStore(db_path=db_path)
        self.conn = self.store.conn
        self.builder = DynamicOLAPBuilder(conn=self.conn)
        self.join_graph = JoinGraph()
        self.slicer = CubeSlicer(conn=self.conn)
        self.latency_profiler = LatencyProfiler(conn=self.conn)
        self.spec: Optional[SchemaSpec] = None
        self.source_id: Optional[str] = None
        self.load_report: Optional[Dict[str, Any]] = None

    def _after_load(self, spec: SchemaSpec, report: Dict[str, Any]) -> Dict[str, Any]:
        self.spec, self.source_id, self.load_report = spec, report["source_id"], report
        self.builder.load_mappings_from_schema(spec, resolve_source(spec, self.conn, report["staging_table"]))
        spine = spec.tables[spec.spine_table]
        num_visits = self.conn.execute(
            f'SELECT COUNT(DISTINCT "{spine.key}") FROM "{spine.name}" WHERE _source_id = ?', [self.source_id]
        ).fetchone()[0]
        raw_rows = self.conn.execute(f'SELECT COUNT(*) FROM "{report["staging_table"]}"').fetchone()[0]
        return {
            "schema": spec.schema_id,
            "source_id": self.source_id,
            "num_visits": num_visits,
            "raw_records_generated": raw_rows,
            "table_counts": self.store.get_table_counts(),
        }

    def ingest_synthetic_dataset(self, num_visits: int = 500, schema_id: str = DEFAULT_SCHEMA) -> Dict[str, Any]:
        """Generates dummy data for a registered schema and loads it."""
        report = self.store.populate_synthetic_data(num_visits=num_visits, schema_id=schema_id)
        return self._after_load(self.store.registry.get(schema_id), report)

    def ingest_csv_dataset(self, csv_path: str, schema_id: Optional[str] = None,
                           source: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        """
        Loads a CSV into a registered schema. Without schema_id, the dataset is
        matched against the registry and loaded only if it fits an existing
        schema as-is; extending or creating a schema is the skill's decision.
        """
        if schema_id is None:
            variables = merge_profile_and_dictionary(profile_csv(csv_path, conn=self.conn), None)
            match = match_registry(variables, self.store.registry)
            if match["recommendation"] != "fit":
                best = match["candidates"][0] if match["candidates"] else {}
                raise ValueError(
                    f"dataset does not fit a registered schema as-is (recommendation: {match['recommendation']}, "
                    f"best: {match['best_schema']}, unmatched: {best.get('unmatched')}); "
                    "model or extend a schema first (see SKILL.md)"
                )
            schema_id = match["best_schema"]
        spec = self.store.registry.get(schema_id)
        report = self.store.loader.load(spec, csv_path, source or csv_path.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                                        force=force)
        if not report["loaded"]:
            raise ValueError(f"preflight gates failed: {[(f['check'], f['target']) for f in report['preflight']['failures']]}")
        return self._after_load(spec, report)

    # ---------- analysis ----------

    def _main_fact(self) -> str:
        return self.spec.dataset_for_source(self.source_id).get("main_fact") or self.spec.main_fact

    def _slice_dimensions(self) -> (str, List[str]):
        """FROM clause over the spine and its dimensions, plus coded attributes to slice by."""
        spec = self.spec
        spine = spec.tables[spec.spine_table]
        alias = {spine.name: "v"}
        joins = [f'"{spine.name}" v']
        for i, link in enumerate(spine.links):
            alias[link] = f"d{i}"
            key = spec.tables[link].key
            joins.append(f'LEFT JOIN "{link}" d{i} ON v."{key}" = d{i}."{key}"')
        dims = []
        for name, a in alias.items():
            for c in spec.tables[name].columns.values():
                if c.role == "attribute" and c.allowed:
                    dims.append(f'{a}."{c.name}"')
        return " ".join(joins), dims

    def run_full_pipeline(self) -> Dict[str, Any]:
        """validate -> join graph -> deep slices -> latency -> reviewer UI, for the loaded source."""
        if self.spec is None:
            raise RuntimeError("load a source first (ingest_synthetic_dataset or ingest_csv_dataset)")

        all_mapped, missing = self.builder.verify_no_variable_left_out_of(self.load_report["staging_table"])

        meta = self.spec.metadata()
        self.join_graph.load_from_metadata(meta)
        main_fact = self._main_fact()
        join_analysis = self.join_graph.analyze_all_paths_from_fact(main_fact)

        checks = run_checks(self.conn, self.spec, self.source_id)
        results = checks["preflight"]["results"] + checks["postload"]["results"]
        scored = [r for r in results if r["severity"] == "error"]
        passed = sum(1 for r in scored if r["passed"])
        val_summary = {
            "total_tests": len(scored),
            "passed": passed,
            "failed": len(scored) - passed,
            "all_passed": passed == len(scored) and len(scored) > 0,
            "details": results,
        }

        base_from, candidate_dims = self._slice_dimensions()
        deep_slices = self.slicer.dig_deepest_slices(
            base_from_clause=base_from,
            candidate_dimensions=candidate_dims[:6],
            metric_sql="COUNT(*)",
            min_depth=2,
            max_depth=4,
        ) if len(candidate_dims) >= 2 else []

        p_const = self.latency_profiler.profile_query(
            f'SELECT COUNT(*) FROM "{main_fact}"', label=f"{main_fact} volume", model_type="constellation"
        )
        benchmark = {"label": "Constellation profiling", "constellation": p_const.to_dict(),
                     "fastest": "constellation"}

        fields_inv = self.builder.get_field_inventory()
        slices_dict = [s.to_dict() for s in deep_slices[:5]]
        review = {"schema": self.spec.schema_id, "source_id": self.source_id, "fields": fields_inv,
                  "validation": val_summary, "join_analysis": join_analysis, "deep_slices": slices_dict,
                  "table_options": sorted(self.spec.tables)}
        html_file = render_review(build=review, dataset=self.spec.dataset_name_for_source(self.source_id))

        return {
            "schema": self.spec.schema_id,
            "main_fact": main_fact,
            "zero_variable_loss": all_mapped,
            "missing_variables": missing,
            "total_variables": len(fields_inv),
            "semantic_validation": val_summary,
            "join_analysis": join_analysis,
            "deepest_slices_count": len(deep_slices),
            "deepest_slice": slices_dict[0] if slices_dict else None,
            "benchmark": benchmark,
            "reviewer_html": html_file,
            "review": review,
        }
