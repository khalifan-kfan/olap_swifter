"""
Dynamic OLAP Model Builder.
Dynamically constructs Star, Snowflake, and Constellation schemas from raw clinical records.
Enforces:
1. Zero Variable Loss: Every field in the raw data is mapped to either a Dimension or Fact.
2. Fact Tables as Many-to-Many Event/Measurement tables.
3. Central Visit Spine linking clinical facts.
4. Field Re-assignment and Interactive Schema Modification.
"""

from typing import Dict, List, Any, Optional, Set, Tuple
import duckdb
import pandas as pd


class FieldDefinition:
    def __init__(self, name: str, data_type: str, role: str, target_table: str, is_key: bool = False, description: str = ""):
        self.name = name
        self.data_type = data_type
        self.role = role  # 'dimension_attribute', 'metric', 'foreign_key', 'primary_key'
        self.target_table = target_table
        self.is_key = is_key
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "role": self.role,
            "target_table": self.target_table,
            "is_key": self.is_key,
            "description": self.description
        }


class DynamicOLAPBuilder:
    """
    Builds and mutates OLAP schemas dynamically with DuckDB, ensuring no variable is left out.
    """

    def __init__(self, conn: Optional[duckdb.DuckDBPyConnection] = None):
        self.conn = conn or duckdb.connect(":memory:")
        self.fields: Dict[str, FieldDefinition] = {}
        self.schema_type: str = "constellation"  # 'star', 'snowflake', 'constellation'
        self.raw_table_name: str = "raw_clinical_records"

    def register_raw_data(self, df: pd.DataFrame, table_name: str = "raw_clinical_records"):
        """Registers the raw flat clinical dataset as ground truth."""
        self.raw_table_name = table_name
        self.conn.register(table_name, df)
        # Create a persistent table in duckdb
        self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM {table_name}")
        self._infer_default_mappings(df)

    def _infer_default_mappings(self, df: pd.DataFrame):
        """
        Infers initial Dimension vs Many-to-Many Fact mappings for all columns.
        Guarantees NO variable is left out.
        """
        self.fields.clear()
        cols = list(df.columns)

        visit_keys = {"visit_id", "encounter_id"}

        for col in cols:
            col_lower = col.lower()
            dtype = str(df[col].dtype)

            # 1. Primary / Spine Keys
            if col_lower in visit_keys:
                target_table = "dim_visit"
                role = "primary_key"
            elif col_lower in ["patient_id", "subject_id", "mrn", "pseudo_mrn"]:
                target_table = "dim_patient"
                role = "primary_key"
            elif col_lower in ["facility_id", "hospital_id"]:
                target_table = "dim_facility"
                role = "primary_key"
            elif col_lower in ["provider_id", "doctor_id"]:
                target_table = "dim_provider"
                role = "primary_key"
            elif col_lower in ["diagnosis_id"]:
                target_table = "dim_diagnosis"
                role = "primary_key"
            elif col_lower in ["medication_id", "drug_id"]:
                target_table = "dim_medication"
                role = "primary_key"
            elif col_lower in ["test_id", "lab_id", "isolate_id"]:
                target_table = "dim_lab_test"
                role = "primary_key"

            # 2. Patient Demographics & Social/Substance History
            elif any(k in col_lower for k in ["substance", "drug_use", "smoking", "tobacco", "alcohol"]):
                target_table = "dim_patient"
                role = "dimension_attribute"
            elif any(k in col_lower for k in ["gender", "sex", "age", "birth", "dob"]):
                target_table = "dim_patient"
                role = "dimension_attribute"
            elif any(k in col_lower for k in ["district", "region", "location"]) and "facility" not in col_lower:
                target_table = "dim_patient"
                role = "dimension_attribute"

            # 3. Facility Attributes
            elif "facility" in col_lower or "hospital" in col_lower:
                target_table = "dim_facility"
                role = "dimension_attribute"

            # 4. Visit/Encounter Spine & Ward
            elif any(k in col_lower for k in ["visit_date", "discharge_date", "visit_type", "admission", "ward", "triage", "urgency"]):
                target_table = "dim_visit"
                role = "dimension_attribute"
            elif any(k in col_lower for k in ["prior_antibiotic", "prior_hospitalization", "readmission"]):
                target_table = "fact_encounters"
                role = "dimension_attribute"

            # 5. Encounter Numeric Metrics
            elif any(k in col_lower for k in ["encounter_cost", "total_cost", "visit_cost"]):
                target_table = "fact_encounters"
                role = "metric"
            elif any(k in col_lower for k in ["stay", "los", "wait_time"]):
                target_table = "fact_encounters"
                role = "metric"

            # 6. Diagnosis Attributes
            elif any(k in col_lower for k in ["diagnosis", "icd", "clinical_category", "comorbidity"]):
                target_table = "dim_diagnosis"
                role = "dimension_attribute"

            # 7. Prescriptions / Medications
            elif any(k in col_lower for k in ["route", "administration", "dosage_form"]):
                target_table = "fact_prescriptions"
                role = "dimension_attribute"
            elif any(k in col_lower for k in ["drug_cost", "prescription_cost", "dose", "duration", "quantity"]):
                target_table = "fact_prescriptions"
                role = "metric"
            elif any(k in col_lower for k in ["medication", "antibiotic_class", "who_aware"]):
                target_table = "dim_medication"
                role = "dimension_attribute"

            # 8. Laboratory & AST / AMR
            elif any(k in col_lower for k in ["zone", "mic"]):
                target_table = "fact_lab_results"
                role = "metric"
            elif any(k in col_lower for k in ["ast", "interpretation", "organism", "isolate", "mdr", "resistant", "susceptib", "tested_antibiotic"]):
                target_table = "fact_lab_results"
                role = "dimension_attribute"
            elif "specimen" in col_lower or col_lower in ["test_name", "test_category"]:
                target_table = "dim_lab_test"
                role = "dimension_attribute"

            # 9. Vitals
            elif any(k in col_lower for k in ["bp", "pulse", "bpm", "temp", "celsius", "spo2", "respiratory", "heart_rate"]):
                target_table = "fact_vitals"
                role = "metric"

            # 10. Default fallback
            elif dtype in ["int64", "float64", "double"] and not col_lower.endswith("_id"):
                target_table = "fact_encounters"
                role = "metric"
            else:
                target_table = "dim_visit"
                role = "dimension_attribute"

            self.fields[col] = FieldDefinition(
                name=col,
                data_type=dtype,
                role=role,
                target_table=target_table,
                is_key=role in ["primary_key", "foreign_key"],
                description=f"Auto-inferred mapping for {col}"
            )

    def switch_field(self, field_name: str, new_target_table: str, new_role: Optional[str] = None):
        """
        Enables user/Playwright to switch any field between dimensions and fact tables,
        or edit its role interactively.
        """
        if field_name not in self.fields:
            raise KeyError(f"Field '{field_name}' not found in registered fields.")

        field = self.fields[field_name]
        field.target_table = new_target_table
        if new_role:
            field.role = new_role
            field.is_key = new_role in ["primary_key", "foreign_key"]

    def get_field_inventory(self) -> List[Dict[str, Any]]:
        """Returns the full inventory of all fields to guarantee zero variable loss."""
        return [f.to_dict() for f in self.fields.values()]

    def verify_no_variable_left_out(self) -> Tuple[bool, List[str]]:
        """Checks that 100% of raw columns have an assigned target table in the model."""
        raw_cols = set(self.conn.execute(f"PRAGMA table_info('{self.raw_table_name}')").df()["name"])
        mapped_cols = set(self.fields.keys())
        missing = list(raw_cols - mapped_cols)
        return (len(missing) == 0, missing)

    def generate_olap_tables(self, schema_type: str = "constellation"):
        """
        Dynamically generates and populates the OLAP tables from the raw data
        according to the current field assignments.
        """
        self.schema_type = schema_type
        # Group fields by target table
        tables: Dict[str, List[FieldDefinition]] = {}
        for f in self.fields.values():
            tables.setdefault(f.target_table, []).append(f)

        # Ensure visit_id is present in all fact tables to preserve the spine!
        has_visit_id = "visit_id" in self.fields

        # Drop dependent tables in proper order (child fact tables first, then dimension tables)
        existing_tables = [t[0] for t in self.conn.execute("SHOW TABLES").fetchall()]
        fact_tables = [t for t in existing_tables if "fact" in t]
        dim_tables = [t for t in existing_tables if "fact" not in t and t != self.raw_table_name]

        for t in fact_tables:
            try:
                self.conn.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass

        for t in dim_tables:
            try:
                self.conn.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass

        for tbl_name, tbl_fields in tables.items():
            field_names = [f.name for f in tbl_fields]
            is_fact = tbl_name.startswith("fact_")

            # In constellation, every fact table must link to visit_id if visit_id exists in raw data
            if is_fact and has_visit_id and "visit_id" not in field_names:
                field_names.insert(0, "visit_id")

            # Also ensure patient_id is present in fact tables if available
            if is_fact and "patient_id" in self.fields and "patient_id" not in field_names:
                field_names.insert(1, "patient_id")

            cols_sql = ", ".join(f'"{col}"' for col in field_names)

            if is_fact:
                # Fact tables are Many-to-Many: select all rows from raw
                self.conn.execute(f"""
                    CREATE OR REPLACE TABLE {tbl_name} AS
                    SELECT {cols_sql}
                    FROM {self.raw_table_name}
                """)
            else:
                # Dimension tables: deduplicate entities
                self.conn.execute(f"""
                    CREATE OR REPLACE TABLE {tbl_name} AS
                    SELECT DISTINCT {cols_sql}
                    FROM {self.raw_table_name}
                """)
