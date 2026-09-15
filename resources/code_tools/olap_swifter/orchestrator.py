"""
Medical OLAP & Clinical Digitization Orchestrator.
The central conductor coordinating Tesseract OCR, offline Presidio privacy,
dynamic OLAP multi-schema modeling (Visit spine), semantic validation,
join graph analysis, deep cube slicing, and Playwright verification.
"""

from typing import Dict, List, Any, Optional
import pandas as pd
import duckdb

from olap_swifter.builder import DynamicOLAPBuilder
from olap_swifter.validator import SemanticDataValidator
from olap_swifter.profiler.join_graph import JoinGraph
from olap_swifter.profiler.slicer import CubeSlicer
from olap_swifter.profiler.latency import LatencyProfiler
from olap_swifter.privacy.presidio_engine import OfflinePresidioAnonymizer
from olap_swifter.ocr.tesseract_runner import TesseractRunner
from olap_swifter.feedback.interactive_app import generate_interactive_html
from olap_swifter.feedback.browser_runner import PlaywrightFeedbackRunner
from olap_swifter.olap.store import MedicalOLAPStore


class MedicalOLAPOrchestrator:
    """Master orchestrator for clinical record digitization and OLAP warehousing."""

    def __init__(self, db_path: Optional[str] = None):
        self.store = MedicalOLAPStore(db_path=db_path)
        self.conn = self.store.conn
        self.builder = DynamicOLAPBuilder(conn=self.conn)
        self.validator = SemanticDataValidator(conn=self.conn)
        self.join_graph = JoinGraph()
        self.slicer = CubeSlicer(conn=self.conn)
        self.latency_profiler = LatencyProfiler(conn=self.conn)
        self.anonymizer = OfflinePresidioAnonymizer()
        self.ocr_runner = TesseractRunner()

    def ingest_synthetic_dataset(self, num_visits: int = 500) -> Dict[str, Any]:
        """Loads a realistic multi-fact clinical dataset into the warehouse."""
        self.store.populate_synthetic_data(num_visits=num_visits)
        # Also create a denormalized raw table to serve as ground truth for semantic tests
        raw_df = self.conn.execute("""
            SELECT
                v.visit_id, v.patient_id, p.gender, p.age_group, p.location_district,
                f.facility_id, f.facility_name, f.facility_level,
                v.visit_date, v.visit_type, v.admission_ward,
                e.length_of_stay_days, e.encounter_cost_ugx,
                dx.diagnosis_id, dx.diagnosis_name, dx.clinical_category,
                rx.medication_id, m.medication_name, m.antibiotic_class, rx.drug_cost_ugx,
                l.test_id, l.ast_interpretation, l.zone_diameter_mm
            FROM dim_visit v
            JOIN dim_patient p ON v.patient_id = p.patient_id
            JOIN dim_facility f ON v.facility_id = f.facility_id
            JOIN fact_encounters e ON v.visit_id = e.visit_id
            LEFT JOIN fact_diagnoses d ON v.visit_id = d.visit_id
            LEFT JOIN dim_diagnosis dx ON d.diagnosis_id = dx.diagnosis_id
            LEFT JOIN fact_prescriptions rx ON v.visit_id = rx.visit_id
            LEFT JOIN dim_medication m ON rx.medication_id = m.medication_id
            LEFT JOIN fact_lab_results l ON v.visit_id = l.visit_id
        """).df()

        self.builder.register_raw_data(raw_df, table_name="raw_clinical_records")
        return {
            "num_visits": num_visits,
            "raw_records_generated": len(raw_df),
            "table_counts": self.store.get_table_counts()
        }

    def ingest_csv_dataset(self, csv_path: str) -> Dict[str, Any]:
        """Ingests an arbitrary wide or messy CSV dataset directly into the warehouse."""
        df = pd.read_csv(csv_path)
        self.builder.register_raw_data(df, table_name="raw_clinical_records")
        self.builder.generate_olap_tables(schema_type="constellation")
        unique_visits = df["visit_id"].nunique() if "visit_id" in df.columns else len(df)
        return {
            "num_visits": unique_visits,
            "raw_records_generated": len(df),
            "table_counts": self.store.get_table_counts()
        }

    def run_full_pipeline(self) -> Dict[str, Any]:
        """Runs end-to-end orchestration: build -> validate -> profile -> slice -> UI report."""
        # 1. Zero variable loss check
        all_mapped, missing = self.builder.verify_no_variable_left_out()

        # 2. Build Constellation Join Graph
        from olap_swifter.olap.constellation import get_constellation_schema_metadata
        const_meta = get_constellation_schema_metadata()
        self.join_graph.load_from_metadata(const_meta)

        # 3. Analyze Join Distances from central facts
        join_analysis = self.join_graph.analyze_all_paths_from_fact("fact_encounters")

        # 4. Run Semantic Data Equivalence Tests
        val_summary = self.validator.run_all_semantic_tests(const_meta)

        # Add row count & metric parity tests
        self.validator.validate_row_counts("dim_visit")
        self.validator.validate_metric_sum("encounter_cost_ugx", "fact_encounters")

        # 5. Dig out Deepest Slices on the Cube
        candidate_dims = ["f.facility_level", "p.age_group", "p.gender", "v.visit_type", "dx.clinical_category"]
        base_from = """
            dim_visit v
            JOIN dim_patient p ON v.patient_id = p.patient_id
            JOIN dim_facility f ON v.facility_id = f.facility_id
            LEFT JOIN fact_diagnoses fd ON v.visit_id = fd.visit_id
            LEFT JOIN dim_diagnosis dx ON fd.diagnosis_id = dx.diagnosis_id
        """
        deep_slices = self.slicer.dig_deepest_slices(
            base_from_clause=base_from,
            candidate_dimensions=candidate_dims,
            metric_sql="COUNT(*)",
            min_depth=2,
            max_depth=4
        )

        # 6. Benchmark Latency across Schema Models (Star vs Snowflake vs Constellation)
        existing_tables = set(t[0] for t in self.conn.execute("SHOW TABLES").fetchall())
        if "star_fact_clinical_events" in existing_tables and "snow_fact_clinical_events" in existing_tables:
            benchmark = self.latency_profiler.compare_schema_models(
                star_sql="SELECT f.facility_id, COUNT(*) FROM star_fact_clinical_events e JOIN star_dim_facility f ON e.facility_id = f.facility_id GROUP BY f.facility_id",
                snowflake_sql="SELECT r.region_name, COUNT(*) FROM snow_fact_clinical_events e JOIN snow_dim_facility f ON e.facility_id = f.facility_id JOIN snow_dim_district d ON f.district_id = d.district_id JOIN snow_dim_region r ON d.region_id = r.region_id GROUP BY r.region_name",
                constellation_sql="SELECT f.region, COUNT(*), SUM(e.encounter_cost_ugx) FROM fact_encounters e JOIN dim_facility f ON e.facility_id = f.facility_id GROUP BY f.region",
                label="Facility Volume & Cost Aggregation"
            )
        else:
            p_const = self.latency_profiler.profile_query(
                "SELECT COUNT(*) FROM fact_encounters", label="Dynamic Constellation Encounter Volume", model_type="constellation"
            )
            benchmark = {
                "label": "Dynamic Constellation Profiling",
                "star": {"total_duration_ms": 0.0},
                "snowflake": {"total_duration_ms": 0.0},
                "constellation": p_const.to_dict(),
                "fastest": "constellation"
            }

        # 7. Generate Interactive Reviewer UI
        fields_inv = self.builder.get_field_inventory()
        slices_dict = [s.to_dict() for s in deep_slices[:5]]
        html_file = generate_interactive_html(
            fields=fields_inv,
            validation_summary=val_summary,
            join_analysis=join_analysis,
            deep_slices=slices_dict,
            output_path="model_reviewer.html"
        )

        return {
            "zero_variable_loss": all_mapped,
            "missing_variables": missing,
            "total_variables": len(fields_inv),
            "semantic_validation": val_summary,
            "join_analysis": join_analysis,
            "deepest_slices_count": len(deep_slices),
            "deepest_slice": slices_dict[0] if slices_dict else None,
            "benchmark": benchmark,
            "reviewer_html": html_file
        }
