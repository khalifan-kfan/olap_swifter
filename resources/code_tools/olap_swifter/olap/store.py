"""
Embedded DuckDB OLAP Store and Synthetic Medical Warehouse Generator.
Loads Star, Snowflake, and Fact Constellation (Galaxy) models.
"""

import duckdb
import random
import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import pandas as pd

from olap_swifter.olap.constellation import CONSTELLATION_DDL, get_constellation_schema_metadata
from olap_swifter.olap.star import STAR_DDL, get_star_schema_metadata
from olap_swifter.olap.snowflake import SNOWFLAKE_DDL, get_snowflake_schema_metadata


class MedicalOLAPStore:
    """Manages embedded DuckDB analytical database for medical OLAP schemas."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or ":memory:"
        self.conn = duckdb.connect(self.db_path)
        self._init_schemas()

    def _init_schemas(self):
        """Initializes all 3 schema models in DuckDB."""
        self.conn.execute(STAR_DDL)
        self.conn.execute(SNOWFLAKE_DDL)
        self.conn.execute(CONSTELLATION_DDL)

    def _insert_rows(self, table_name: str, columns: List[str], rows: List[Any]):
        """Helper to register and insert rows using pandas DataFrames."""
        if not rows:
            return
        df = pd.DataFrame(rows, columns=columns)
        tmp_name = f"tmp_{table_name}"
        self.conn.register(tmp_name, df)
        cols_str = ", ".join(f'"{c}"' for c in columns)
        self.conn.execute(f"INSERT OR REPLACE INTO {table_name} ({cols_str}) SELECT {cols_str} FROM {tmp_name};")
        self.conn.unregister(tmp_name)

    def populate_synthetic_data(self, num_visits: int = 1000):
        """
        Populates all three schemas with realistic, internally consistent
        clinical data from LMIC healthcare context (IDI / AMRDB setting).
        """
        # 1. Facilities
        facilities = [
            ("FAC-001", "Mulago National Referral Hospital", "National Referral", "Kampala", "Central"),
            ("FAC-002", "Arua Regional Referral Hospital", "Regional Referral", "Arua", "Northern"),
            ("FAC-003", "Jinja Regional Referral Hospital", "Regional Referral", "Jinja", "Eastern"),
            ("FAC-004", "Mbarara Regional Referral Hospital", "Regional Referral", "Mbarara", "Western"),
            ("FAC-005", "Gulu Regional Referral Hospital", "Regional Referral", "Gulu", "Northern"),
            ("FAC-006", "Entebbe General Hospital", "District Hospital", "Wakiso", "Central"),
            ("FAC-007", "Kisenyi Health Center IV", "Health Center IV", "Kampala", "Central"),
        ]
        fac_cols = ["facility_id", "facility_name", "facility_level", "district", "region"]
        self._insert_rows("dim_facility", fac_cols, facilities)

        star_facs = [(f[0], f[1], f[2], f[3]) for f in facilities]
        self._insert_rows("star_dim_facility", ["facility_id", "facility_name", "facility_level", "district"], star_facs)

        # Snowflake facilities & hierarchies
        regions = [("REG-1", "Central", "Uganda"), ("REG-2", "Northern", "Uganda"), ("REG-3", "Eastern", "Uganda"), ("REG-4", "Western", "Uganda")]
        self._insert_rows("snow_dim_region", ["region_id", "region_name", "country"], regions)

        districts = [
            ("DIST-1", "Kampala", "REG-1"), ("DIST-2", "Wakiso", "REG-1"),
            ("DIST-3", "Arua", "REG-2"), ("DIST-4", "Gulu", "REG-2"),
            ("DIST-5", "Jinja", "REG-3"), ("DIST-6", "Mbarara", "REG-4")
        ]
        self._insert_rows("snow_dim_district", ["district_id", "district_name", "region_id"], districts)

        fac_types = [("FT-1", "National Referral", "Tier 1"), ("FT-2", "Regional Referral", "Tier 2"), ("FT-3", "District Hospital", "Tier 3"), ("FT-4", "Health Center IV", "Tier 4")]
        self._insert_rows("snow_dim_facility_type", ["facility_type_id", "level_name", "tier"], fac_types)

        snow_facs = [
            ("FAC-001", "Mulago National Referral Hospital", "FT-1", "DIST-1"),
            ("FAC-002", "Arua Regional Referral Hospital", "FT-2", "DIST-3"),
            ("FAC-003", "Jinja Regional Referral Hospital", "FT-2", "DIST-5"),
            ("FAC-004", "Mbarara Regional Referral Hospital", "FT-2", "DIST-6"),
            ("FAC-005", "Gulu Regional Referral Hospital", "FT-2", "DIST-4"),
            ("FAC-006", "Entebbe General Hospital", "FT-3", "DIST-2"),
            ("FAC-007", "Kisenyi Health Center IV", "FT-4", "DIST-1"),
        ]
        self._insert_rows("snow_dim_facility", ["facility_id", "facility_name", "facility_type_id", "district_id"], snow_facs)

        # 2. Providers
        providers = [
            ("PRV-101", "Medical Officer", "Internal Medicine"),
            ("PRV-102", "Clinical Officer", "Outpatient Care"),
            ("PRV-103", "Consultant Physician", "Infectious Diseases"),
            ("PRV-104", "Senior Nursing Officer", "Emergency"),
            ("PRV-105", "Senior Microbiologist", "Microbiology Laboratory")
        ]
        self._insert_rows("dim_provider", ["provider_id", "provider_role", "department"], providers)

        # 3. Diagnoses
        diagnoses = [
            ("DX-001", "A41.9", "Sepsis, unspecified organism", "Infectious"),
            ("DX-002", "J18.9", "Pneumonia, unspecified organism", "Respiratory"),
            ("DX-003", "N39.0", "Urinary tract infection, site not specified", "Infectious"),
            ("DX-004", "B54", "Unspecified malaria", "Infectious"),
            ("DX-005", "A09", "Infectious gastroenteritis and colitis", "Gastrointestinal"),
            ("DX-006", "I10", "Essential (primary) hypertension", "Cardiovascular"),
            ("DX-007", "E11.9", "Type 2 diabetes mellitus without complications", "Endocrine"),
            ("DX-008", "A15.0", "Tuberculosis of lung", "Infectious")
        ]
        dx_cols = ["diagnosis_id", "icd10_code", "diagnosis_name", "clinical_category"]
        self._insert_rows("dim_diagnosis", dx_cols, diagnoses)
        self._insert_rows("star_dim_diagnosis", ["diagnosis_id", "icd10_code", "diagnosis_name", "category"], diagnoses)

        # Snowflake diagnosis categories
        dx_cats = [
            ("CAT-1", "Infectious", "Chapter I"), ("CAT-2", "Respiratory", "Chapter X"),
            ("CAT-3", "Cardiovascular", "Chapter IX"), ("CAT-4", "Endocrine", "Chapter IV"),
            ("CAT-5", "Gastrointestinal", "Chapter XI")
        ]
        self._insert_rows("snow_dim_diagnosis_category", ["category_id", "category_name", "icd_chapter"], dx_cats)
        snow_dx = [
            ("DX-001", "A41.9", "Sepsis, unspecified organism", "CAT-1"),
            ("DX-002", "J18.9", "Pneumonia, unspecified organism", "CAT-2"),
            ("DX-003", "N39.0", "Urinary tract infection", "CAT-1"),
            ("DX-004", "B54", "Unspecified malaria", "CAT-1"),
            ("DX-005", "A09", "Infectious gastroenteritis", "CAT-5"),
            ("DX-006", "I10", "Essential hypertension", "CAT-3"),
            ("DX-007", "E11.9", "Type 2 diabetes", "CAT-4"),
            ("DX-008", "A15.0", "Tuberculosis of lung", "CAT-1")
        ]
        self._insert_rows("snow_dim_diagnosis", ["diagnosis_id", "icd10_code", "diagnosis_name", "category_id"], snow_dx)

        # 4. Medications
        medications = [
            ("MED-001", "Ceftriaxone 1g Inj", "J01DD04", "Cephalosporins", "Watch"),
            ("MED-002", "Amoxicillin/Clavulanic Acid 625mg", "J01CR02", "Penicillins", "Access"),
            ("MED-003", "Ciprofloxacin 500mg Tab", "J01MA02", "Fluoroquinolones", "Watch"),
            ("MED-004", "Gentamicin 80mg Inj", "J01GB03", "Aminoglycosides", "Access"),
            ("MED-005", "Meropenem 1g Inj", "J01DH02", "Carbapenems", "Reserve"),
            ("MED-006", "Artemether/Lumefantrine (Coartem)", "P01BF01", "Non-Antibiotic", "Not Applicable"),
            ("MED-007", "Paracetamol 500mg Tab", "N02BE01", "Non-Antibiotic", "Not Applicable"),
            ("MED-008", "Metronidazole 400mg Tab", "J01XD01", "Nitroimidazoles", "Access")
        ]
        self._insert_rows("dim_medication", ["medication_id", "medication_name", "atc_code", "antibiotic_class", "who_awares_category"], medications)

        # 5. Lab Tests
        lab_tests = [
            ("LAB-001", "Blood Culture & Sensitivity", "Microbiology / AST", "Blood"),
            ("LAB-002", "Urine Culture & Sensitivity", "Microbiology / AST", "Urine"),
            ("LAB-003", "Complete Blood Count (CBC)", "Hematology", "Blood"),
            ("LAB-004", "Serum Creatinine", "Biochemistry", "Blood"),
            ("LAB-005", "Malaria Rapid Diagnostic Test (RDT)", "Serology", "Blood"),
            ("LAB-006", "Sputum GeneXpert MTB/RIF", "Microbiology / AST", "Sputum")
        ]
        self._insert_rows("dim_lab_test", ["test_id", "test_name", "test_category", "specimen_type"], lab_tests)

        # 6. Dates
        start_date = datetime.date(2025, 1, 1)
        date_rows = []
        star_date_rows = []
        for d_offset in range(365):
            curr_date = start_date + datetime.timedelta(days=d_offset)
            date_rows.append((
                curr_date,
                curr_date.strftime("%A"),
                curr_date.strftime("%B"),
                f"Q{(curr_date.month - 1) // 3 + 1}",
                curr_date.year,
                curr_date.weekday() >= 5
            ))
            star_date_rows.append((curr_date, curr_date.strftime("%A"), curr_date.strftime("%B"), curr_date.year))

        self._insert_rows("dim_date", ["date_id", "day_of_week", "month_name", "quarter", "year", "is_weekend"], date_rows)
        self._insert_rows("star_dim_date", ["date_id", "day_of_week", "month_name", "year"], star_date_rows)
        self._insert_rows("snow_dim_date", ["date_id", "day_of_week", "month_name", "year"], star_date_rows)

        # 7. Patients
        num_patients = max(50, num_visits // 3)
        patient_rows = []
        star_patients = []
        snow_patients = []
        age_groups = ['0-4', '5-14', '15-24', '25-49', '50-64', '65+']
        dist_options = [("Kampala", "Central", "DIST-1"), ("Wakiso", "Central", "DIST-2"), ("Arua", "Northern", "DIST-3"), ("Gulu", "Northern", "DIST-4"), ("Jinja", "Eastern", "DIST-5"), ("Mbarara", "Western", "DIST-6")]

        for p_idx in range(1, num_patients + 1):
            p_id = f"PAT-{p_idx:05d}"
            p_mrn = f"MRN-UG-{random.randint(100000, 999999)}"
            gender = random.choice(["Female", "Male"])
            age_grp = random.choice(age_groups)
            dist_name, reg_name, dist_code = random.choice(dist_options)
            patient_rows.append((p_id, p_mrn, gender, age_grp, dist_name, reg_name))
            star_patients.append((p_id, gender, age_grp, dist_name, reg_name))
            snow_patients.append((p_id, gender, age_grp, dist_code))

        self._insert_rows("dim_patient", ["patient_id", "pseudo_mrn", "gender", "age_group", "location_district", "location_region"], patient_rows)
        self._insert_rows("star_dim_patient", ["patient_id", "gender", "age_group", "district", "region"], star_patients)
        self._insert_rows("snow_dim_patient", ["patient_id", "gender", "age_group", "district_id"], snow_patients)

        # 8. Encounters / Visits & Constellation Facts
        visit_types = ["Inpatient", "Outpatient", "Emergency", "Antenatal"]
        urgencies = ["Immediate", "Very Urgent", "Urgent", "Standard"]
        wards = ["Medical Ward", "Pediatric Ward", "Surgical Ward", "ICU", "OPD Clinic", "Maternity Ward"]
        organisms = ["Klebsiella pneumoniae", "Escherichia coli", "Staphylococcus aureus", "Pseudomonas aeruginosa", "Acinetobacter baumannii"]
        antibiotics_tested = ["Ceftriaxone", "Ciprofloxacin", "Gentamicin", "Meropenem", "Amikacin"]

        visit_rows = []
        encounter_fact_rows = []
        diagnosis_fact_rows = []
        prescription_fact_rows = []
        lab_fact_rows = []
        vital_fact_rows = []

        star_event_rows = []
        snow_event_rows = []

        for v_idx in range(1, num_visits + 1):
            v_id = f"VST-{v_idx:06d}"
            p_id = f"PAT-{random.randint(1, num_patients):05d}"
            f_id = random.choice(facilities)[0]
            prv_id = random.choice(providers)[0]
            rand_date = start_date + datetime.timedelta(days=random.randint(0, 360))
            v_type = random.choice(visit_types)
            urgency = random.choice(urgencies)
            ward = random.choice(wards)
            los = round(random.uniform(0.5, 14.0), 1) if v_type == "Inpatient" else 0.0
            disch_date = rand_date + datetime.timedelta(days=int(los)) if los > 0 else rand_date

            visit_rows.append((v_id, p_id, f_id, prv_id, rand_date, disch_date, v_type, urgency, ward))

            # Fact Encounters
            cost_encounter = random.uniform(15000, 350000)
            encounter_fact_rows.append((
                f"ENC-{v_idx:06d}", v_id, p_id, f_id, rand_date, los,
                random.uniform(10, 180), cost_encounter
            ))

            # Fact Diagnoses (1 to 2 per visit)
            chosen_dx = random.sample(diagnoses, k=random.choice([1, 2]))
            for d_i, dx_item in enumerate(chosen_dx):
                diagnosis_fact_rows.append((
                    f"DXF-{v_idx:06d}-{d_i}", v_id, p_id, dx_item[0], rand_date,
                    (d_i == 0), random.choice(["Confirmed", "Presumptive"])
                ))

            # Fact Prescriptions (1 to 3 per visit)
            num_meds = random.randint(1, 3)
            total_med_cost = 0.0
            for m_i in range(num_meds):
                med_item = random.choice(medications)
                med_cost = random.uniform(5000, 60000)
                total_med_cost += med_cost
                prescription_fact_rows.append((
                    f"RXF-{v_idx:06d}-{m_i}", v_id, p_id, med_item[0], rand_date,
                    float(random.choice([250, 500, 1000])), random.randint(3, 14),
                    random.randint(10, 30), med_cost, (med_item[3] in ["Cephalosporins", "Carbapenems"])
                ))

            # Fact Lab Results (50% of visits have labs)
            has_abnormal = False
            has_resistant = False
            if random.random() < 0.6:
                lab_item = random.choice(lab_tests)
                is_ast = "Microbiology" in lab_item[2]
                org = random.choice(organisms) if is_ast else None
                atb = random.choice(antibiotics_tested) if is_ast else None
                zone = round(random.uniform(6.0, 30.0), 1) if is_ast else None
                mic = round(random.uniform(0.25, 64.0), 2) if is_ast else None
                sir = random.choice(["S", "I", "R"]) if is_ast else None
                if sir == "R":
                    has_resistant = True
                abnormal = (sir in ["I", "R"]) if is_ast else (random.random() < 0.3)
                if abnormal:
                    has_abnormal = True

                lab_fact_rows.append((
                    f"LBF-{v_idx:06d}", v_id, p_id, lab_item[0], rand_date,
                    round(random.uniform(3.0, 18.0), 1), "g/dL" if "CBC" in lab_item[1] else "mm",
                    abnormal, org, atb, zone, mic, sir, (sir == "R" and random.random() < 0.4)
                ))

            # Fact Vitals
            vital_fact_rows.append((
                f"VTF-{v_idx:06d}", v_id, p_id, rand_date,
                random.randint(95, 170), random.randint(60, 105),
                random.randint(60, 120), round(random.uniform(36.1, 39.5), 1),
                random.randint(14, 28), round(random.uniform(91.0, 99.0), 1)
            ))

            # Single Star event
            primary_dx = chosen_dx[0][0]
            star_event_rows.append((
                f"STAREVT-{v_idx:06d}", v_id, p_id, f_id, rand_date, primary_dx,
                v_type, los, num_meds, cost_encounter + total_med_cost,
                has_abnormal, has_resistant
            ))

            # Snowflake event
            snow_event_rows.append((
                f"SNOWEVT-{v_idx:06d}", v_id, p_id, f_id, rand_date, primary_dx,
                cost_encounter + total_med_cost, los
            ))

        # Bulk load into DuckDB
        self._insert_rows("dim_visit", ["visit_id", "patient_id", "facility_id", "provider_id", "visit_date", "discharge_date", "visit_type", "triage_urgency", "admission_ward"], visit_rows)
        self._insert_rows("fact_encounters", ["encounter_fact_id", "visit_id", "patient_id", "facility_id", "visit_date", "length_of_stay_days", "wait_time_minutes", "encounter_cost_ugx"], encounter_fact_rows)
        self._insert_rows("fact_diagnoses", ["diagnosis_fact_id", "visit_id", "patient_id", "diagnosis_id", "visit_date", "is_primary_diagnosis", "certainty_level"], diagnosis_fact_rows)
        self._insert_rows("fact_prescriptions", ["prescription_fact_id", "visit_id", "patient_id", "medication_id", "visit_date", "dose_mg", "duration_days", "quantity_dispensed", "drug_cost_ugx", "is_broad_spectrum"], prescription_fact_rows)
        self._insert_rows("fact_lab_results", ["lab_fact_id", "visit_id", "patient_id", "test_id", "visit_date", "numeric_result", "result_unit", "abnormal_flag", "isolate_organism", "tested_antibiotic", "zone_diameter_mm", "mic_ug_ml", "ast_interpretation", "is_mdr_isolate"], lab_fact_rows)
        self._insert_rows("fact_vitals", ["vital_fact_id", "visit_id", "patient_id", "visit_date", "systolic_bp", "diastolic_bp", "pulse_bpm", "temp_celsius", "respiratory_rate", "spo2_percent"], vital_fact_rows)

        self._insert_rows("star_fact_clinical_events", ["event_id", "visit_id", "patient_id", "facility_id", "date_id", "primary_diagnosis_id", "visit_type", "length_of_stay_days", "total_medication_count", "total_cost_ugx", "has_abnormal_lab", "has_resistant_isolate"], star_event_rows)
        self._insert_rows("snow_fact_clinical_events", ["event_id", "visit_id", "patient_id", "facility_id", "date_id", "primary_diagnosis_id", "total_cost_ugx", "length_of_stay_days"], snow_event_rows)

    def query(self, sql: str) -> pd.DataFrame:
        """Executes a SQL query and returns a pandas DataFrame."""
        return self.conn.execute(sql).df()

    def get_table_counts(self) -> Dict[str, int]:
        """Returns row counts for all tables across the three schema models."""
        tables = [
            "dim_visit", "dim_patient", "dim_facility", "dim_provider", "dim_date",
            "dim_diagnosis", "dim_medication", "dim_lab_test",
            "fact_encounters", "fact_diagnoses", "fact_prescriptions",
            "fact_lab_results", "fact_vitals",
            "star_fact_clinical_events", "snow_fact_clinical_events"
        ]
        counts = {}
        for t in tables:
            try:
                cnt = self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                counts[t] = cnt
            except Exception:
                pass
        return counts
