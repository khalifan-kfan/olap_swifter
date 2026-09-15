"""
Tests for dynamic messy clinical CSV datasets.
Validates pre-ETL gates, DuckDB schema generation, zero variable loss,
and AMR I/R/S interpretation queries across sample_wide_clinical.csv and sample_amr_clinical.csv.
"""

import pytest
import pandas as pd
from pathlib import Path
from olap_swifter.builder import DynamicOLAPBuilder
from olap_swifter.validator import SemanticDataValidator


def test_sample_wide_clinical_ingestion_and_etl():
    csv_path = Path("resources/datasets/sample_wide_clinical.csv")
    if not csv_path.exists():
        csv_path = Path("sample_wide_clinical.csv")
    assert csv_path.exists(), "sample_wide_clinical.csv not found"

    df = pd.read_csv(csv_path)

    # 1. Pre-ETL Invariants
    assert "visit_id" in df.columns
    assert df["visit_id"].isnull().sum() == 0, "Visit spine has null keys"
    assert "substance_use_history" in df.columns
    assert "drug_administration_route" in df.columns
    assert "prior_antibiotic_use_30d" in df.columns

    # 2. Dynamic Schema Discovery & Zero Variable Loss
    builder = DynamicOLAPBuilder()
    builder.register_raw_data(df, table_name="raw_clinical_records")
    all_mapped, missing = builder.verify_no_variable_left_out()
    assert all_mapped is True, f"Missing columns: {missing}"

    # Verify patient drug exposure mapped to dim_patient
    assert builder.fields["substance_use_history"].target_table == "dim_patient"
    assert builder.fields["drug_cost_ugx"].target_table == "fact_prescriptions"
    assert builder.fields["encounter_cost_ugx"].target_table == "fact_encounters"

    # 3. DuckDB Simple ETL
    builder.generate_olap_tables(schema_type="constellation")

    # 4. Semantic Parity Invariant
    raw_cost = df["encounter_cost_ugx"].sum()
    olap_cost = builder.conn.execute("SELECT SUM(encounter_cost_ugx) FROM fact_encounters").fetchone()[0]
    assert abs(raw_cost - olap_cost) < 1e-3, f"Cost parity delta: {abs(raw_cost - olap_cost)}"

    # 5. Referential Integrity: Fact to Visit Spine
    orphan_facts = builder.conn.execute("""
        SELECT COUNT(*) FROM fact_encounters f
        LEFT JOIN dim_visit v ON f.visit_id = v.visit_id
        WHERE v.visit_id IS NULL
    """).fetchone()[0]
    assert orphan_facts == 0, "Found orphan fact records unlinked to dim_visit"


def test_sample_amr_clinical_ingestion_and_ast_queries():
    csv_path = Path("resources/datasets/sample_amr_clinical.csv")
    if not csv_path.exists():
        csv_path = Path("sample_amr_clinical.csv")
    assert csv_path.exists(), "sample_amr_clinical.csv not found"

    df = pd.read_csv(csv_path)

    # 1. Pre-ETL Invariants
    assert "ast_interpretation" in df.columns
    allowed_interps = {"I", "R", "S"}
    unique_interps = set(df["ast_interpretation"].dropna().unique())
    assert unique_interps.issubset(allowed_interps), f"Unexpected AST interpretations: {unique_interps}"
    assert "I" in unique_interps and "R" in unique_interps and "S" in unique_interps

    # 2. Dynamic Schema Discovery & DuckDB ETL
    builder = DynamicOLAPBuilder()
    builder.register_raw_data(df, table_name="raw_clinical_records")
    all_mapped, missing = builder.verify_no_variable_left_out()
    assert all_mapped is True

    builder.generate_olap_tables(schema_type="constellation")

    # 3. Dynamic AMR Surveillance Query in DuckDB
    # Calculate % R, % I, % S rates across organisms
    amr_breakdown = builder.conn.execute("""
        SELECT 
            ast_interpretation,
            COUNT(*) AS isolate_count,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS pct
        FROM fact_lab_results
        GROUP BY ast_interpretation
        ORDER BY ast_interpretation
    """).df()

    assert len(amr_breakdown) == 3, "Expected rows for I, R, and S"
    assert abs(amr_breakdown["pct"].sum() - 100.0) < 0.1, "Percentages must sum to 100%"

    # 4. Zero Orphan Foreign Keys
    orphan_labs = builder.conn.execute("""
        SELECT COUNT(*) FROM fact_lab_results l
        LEFT JOIN dim_visit v ON l.visit_id = v.visit_id
        WHERE v.visit_id IS NULL
    """).fetchone()[0]
    assert orphan_labs == 0
