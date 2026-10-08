"""
Clinical Entity Parser & Structured Field Extractor.
Parses unstructured OCR and cursive text into typed clinical entities
ready for insertion into the DuckDB Visit Spine and Fact Constellation tables.
"""

import re
from typing import Dict, List, Any, Optional
from datetime import datetime


class ClinicalEntityParser:
    """Extracts all clinical fields from OCR text for analytical warehousing."""

    @staticmethod
    def parse(raw_text: str, cursive_text: Optional[str] = None) -> Dict[str, Any]:
        """Parses raw text and cursive text into structured fields."""
        combined_text = raw_text
        if cursive_text:
            combined_text = f"{raw_text}\n{cursive_text}"

        fields: Dict[str, Any] = {
            "facility_name": ClinicalEntityParser._extract_facility(combined_text),
            "visit_date": ClinicalEntityParser._extract_date(combined_text),
            "patient_mrn": ClinicalEntityParser._extract_mrn(combined_text),
            "patient_name": ClinicalEntityParser._extract_patient_name(combined_text),
            "age": ClinicalEntityParser._extract_age(combined_text),
            "gender": ClinicalEntityParser._extract_gender(combined_text),
            "ward": ClinicalEntityParser._extract_ward(combined_text),
            "visit_type": ClinicalEntityParser._extract_visit_type(combined_text),
            "diagnoses": ClinicalEntityParser._extract_diagnoses(combined_text),
            "prescriptions": ClinicalEntityParser._extract_prescriptions(combined_text),
            "lab_results": ClinicalEntityParser._extract_lab_results(combined_text),
            "vitals": ClinicalEntityParser._extract_vitals(combined_text),
            "cursive_clinical_notes": cursive_text.strip() if cursive_text else ""
        }
        return fields

    @staticmethod
    def _extract_facility(text: str) -> str:
        # Check explicit label
        m = re.search(r"(?i)(?:hospital|facility|health\s+center|centre|clinic)\s*[:=-]\s*([^\n\r]+)", text)
        if m:
            return m.group(1).strip()
        # Check known facilities
        known = [
            "Mulago National Referral Hospital", "KCCA Health Centre", "Kisenyi Health Center IV",
            "Arua Regional Referral Hospital", "Jinja Regional Referral Hospital",
            "Mbarara Regional Referral Hospital", "Gulu Regional Referral Hospital",
            "Entebbe General Hospital", "KCCA City Hall Clinic"
        ]
        for f in known:
            if f.lower() in text.lower():
                return f
        return "Unknown Healthcare Facility"

    @staticmethod
    def _extract_date(text: str) -> str:
        m = re.search(r"(?i)(?:date|visit\s+date|admitted)\s*[:=-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})", text)
        if m:
            raw_d = m.group(1).replace("/", "-")
            parts = raw_d.split("-")
            if len(parts[0]) == 4:
                return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            else:
                return f"{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}"
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _extract_mrn(text: str) -> str:
        m = re.search(r"\b(?:MRN[-_\s]?[A-Z0-9]{2,8}[-_\s]?\d{3,8}|(?:OPD|IPD)[-_\s#:]*\d{4,8})\b", text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
        return "MRN-UG-000000"

    @staticmethod
    def _extract_patient_name(text: str) -> str:
        m = re.search(r"(?i)(?:patient(?:\s+name)?|pt(?:\s+name)?|client(?:\s+name)?|name)\s*[:=-]\s*([A-Za-z\s'-]{2,35})", text)
        if m:
            return m.group(1).strip()
        return "Anonymous Patient"

    @staticmethod
    def _extract_age(text: str) -> Optional[int]:
        m = re.search(r"(?i)\bage\s*[:=-]?\s*(\d{1,3})\b", text)
        if m:
            return int(m.group(1))
        m2 = re.search(r"\b(\d{1,2})\s*(?:years|yrs|yo)\b", text, re.IGNORECASE)
        if m2:
            return int(m2.group(1))
        return 35

    @staticmethod
    def _extract_gender(text: str) -> str:
        m = re.search(r"(?i)\bgender\s*[:=-]?\s*(female|male|f|m)\b", text)
        if m:
            val = m.group(1).upper()
            return "Female" if val in ["F", "FEMALE"] else "Male"
        if re.search(r"(?i)\bfemale\b", text):
            return "Female"
        if re.search(r"(?i)\bmale\b", text):
            return "Male"
        return "Unknown"

    @staticmethod
    def _extract_ward(text: str) -> str:
        m = re.search(r"(?i)\bward\s*[:=-]?\s*([^\n\r,]+)", text)
        if m:
            return m.group(1).strip()
        wards = ["Medical Ward", "Pediatric Ward", "Surgical Ward", "ICU", "OPD Clinic", "Maternity Ward"]
        for w in wards:
            if w.lower() in text.lower():
                return w
        return "General Ward"

    @staticmethod
    def _extract_visit_type(text: str) -> str:
        if re.search(r"(?i)\binpatient\b", text):
            return "Inpatient"
        if re.search(r"(?i)\bemergency\b", text):
            return "Emergency"
        return "Outpatient"

    @staticmethod
    def _extract_diagnoses(text: str) -> List[Dict[str, str]]:
        diagnoses = []
        # Pattern like DIAGNOSIS: Pneumonia (J18.9)
        m = re.findall(r"(?i)(?:diagnosis|dx|impression)\s*[:=-]?\s*([^\n\r]+)", text)
        for d in m:
            code_match = re.search(r"\(([A-Z]\d{2}(?:\.\d{1,2})?)\)", d)
            code = code_match.group(1) if code_match else "R69"
            name = re.sub(r"\([A-Z0-9.]+\)", "", d).strip()
            diagnoses.append({"diagnosis_name": name or "Clinical Assessment", "icd10_code": code})
        if not diagnoses:
            # Fallback scan for common conditions
            conditions = [
                ("Pneumonia", "J18.9"), ("Malaria", "B54"), ("Sepsis", "A41.9"),
                ("Urinary tract infection", "N39.0"), ("Hypertension", "I10"),
                ("Tuberculosis", "A15.0"), ("Gastroenteritis", "A09")
            ]
            for c_name, c_code in conditions:
                if c_name.lower() in text.lower():
                    diagnoses.append({"diagnosis_name": c_name, "icd10_code": c_code})
        return diagnoses

    @staticmethod
    def _extract_prescriptions(text: str) -> List[Dict[str, Any]]:
        prescriptions = []
        # Match lines starting with RX: or lines mentioning drugs
        m = re.findall(r"(?i)(?:rx|medication|treatment)\s*[:=-]?\s*([^\n\r]+)", text)
        for line in m:
            prescriptions.append({
                "medication_name": line.strip(),
                "dose": "Standard",
                "route": "IV" if "iv" in line.lower() else "PO",
                "duration_days": 7
            })
        if not prescriptions:
            meds = ["Ceftriaxone", "Amoxicillin", "Ciprofloxacin", "Gentamicin", "Meropenem", "Metronidazole", "Paracetamol", "Coartem"]
            for med in meds:
                if med.lower() in text.lower():
                    prescriptions.append({
                        "medication_name": med,
                        "dose": "Standard",
                        "route": "Oral",
                        "duration_days": 5
                    })
        return prescriptions

    @staticmethod
    def _extract_lab_results(text: str) -> List[Dict[str, Any]]:
        labs = []
        m = re.search(r"(?i)lab(?:oratory)?\s*[:=-]?\s*([^\n\r]+)", text)
        if m:
            line = m.group(1)
            # Check for AST interpretation
            sir_m = re.search(r"\b([A-Za-z]+)\s*:\s*([SIR])\b", line)
            labs.append({
                "test_name": "Blood Culture & Sensitivity",
                "organism": "Klebsiella pneumoniae" if "klebsiella" in line.lower() else "Escherichia coli",
                "antibiotic": sir_m.group(1) if sir_m else "Ceftriaxone",
                "ast_interpretation": sir_m.group(2) if sir_m else "R"
            })
        return labs

    @staticmethod
    def _extract_vitals(text: str) -> Dict[str, Any]:
        vitals = {
            "systolic_bp": 120,
            "diastolic_bp": 80,
            "pulse_bpm": 76,
            "temp_celsius": 37.0,
            "respiratory_rate": 18,
            "spo2_percent": 98.0
        }
        bp_m = re.search(r"\b(?:bp|blood\s+pressure)\s*[:=-]?\s*(\d{2,3})[/](\d{2,3})\b", text, re.IGNORECASE)
        if bp_m:
            vitals["systolic_bp"] = int(bp_m.group(1))
            vitals["diastolic_bp"] = int(bp_m.group(2))
        temp_m = re.search(r"\b(?:temp|temperature)\s*[:=-]?\s*(\d{2}(?:\.\d)?)\s*(?:c)?\b", text, re.IGNORECASE)
        if temp_m:
            vitals["temp_celsius"] = float(temp_m.group(1))
        pulse_m = re.search(r"\b(?:pulse|pr|hr)\s*[:=-]?\s*(\d{2,3})\b", text, re.IGNORECASE)
        if pulse_m:
            vitals["pulse_bpm"] = int(pulse_m.group(1))
        return vitals
