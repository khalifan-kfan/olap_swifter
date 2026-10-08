"""
Offline Microsoft Presidio De-Identification Engine.
Ensures patient privacy by detecting and masking patient info, contacts, and IDs
while preserving hospital/facility names and clinical context for analytical OLAP processing.
"""

import re
from typing import Dict, List, Any, Optional

try:
    import spacy
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    from presidio_analyzer.nlp_engine import SpacyNlpEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    PRESIDIO_AVAILABLE = True
except ImportError:
    PRESIDIO_AVAILABLE = False


FACILITY_EXCLUSION_KEYWORDS = {
    "hospital", "referral", "clinic", "health center", "health centre",
    "kcca", "kampala capital city authority", "ministry", "moh",
    "dispensary", "medical center", "medical centre", "infirmary",
    "department", "ward", "laboratory", "national referral"
}


class OfflinePresidioAnonymizer:
    """
    Offline PII detection and redaction using Microsoft Presidio.
    Focuses strictly on Patient Info (names), Contact info (phones, email),
    and Identifiers (MRN, NIN), while ignoring hospital/facility metadata.
    """

    def __init__(self):
        if PRESIDIO_AVAILABLE:
            try:
                nlp_engine = SpacyNlpEngine()
                nlp_engine.nlp = {"en": spacy.blank("en")}
                self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
            except Exception:
                self.analyzer = None
            self.anonymizer = AnonymizerEngine()
            self._register_custom_clinical_recognizers()
        else:
            self.analyzer = None
            self.anonymizer = None

    def _register_custom_clinical_recognizers(self):
        """Adds custom recognizers for patient identifiers and names."""
        if not self.analyzer:
            return

        # 1. Full Patient MRN Pattern (e.g. MRN-UG-482910, MRN/2025/12345, OPD-98124)
        mrn_pattern = Pattern(
            name="mrn_pattern",
            regex=r"\b(?:MRN[-_\s]?[A-Z0-9]{2,8}[-_\s]?\d{3,8}|(?:OPD|IPD)[-_\s#:]*\d{4,8})\b",
            score=0.9
        )
        mrn_recognizer = PatternRecognizer(supported_entity="MEDICAL_RECORD_NUMBER", patterns=[mrn_pattern])
        self.analyzer.registry.add_recognizer(mrn_recognizer)

        # 2. National ID / NIN Pattern (Ugandan NIN format e.g. CM95012104XYZ1 or NIN: CM...)
        nin_pattern = Pattern(
            name="nin_pattern",
            regex=r"\b(?:NIN[:\s-]*)?(?:[A-Z]{2}\d{7}[A-Z]{4}|(?:CM|CF)[\s-]?\d{8}[\s-]?[A-Z0-9]{4})\b",
            score=0.9
        )
        nin_recognizer = PatternRecognizer(supported_entity="NATIONAL_ID", patterns=[nin_pattern])
        self.analyzer.registry.add_recognizer(nin_recognizer)

        # 3. Patient Name Context Pattern (e.g., Patient Name: John Kato, Patient's Last Name: Nakato, First Name: Sarah)
        name_pattern = Pattern(
            name="patient_name_context",
            regex=r"(?i:(?:patient(?:'s)?(?:\s+(?:full|last|first)?\s*name)?|pt(?:\s+name)?|client(?:\s+name)?|first\s+name|last\s+name|next\s+of\s+kin|nok))\s*[:=-]\s*([A-Za-z'-]{2,20})",
            score=0.85
        )
        name_recognizer = PatternRecognizer(supported_entity="PERSON", patterns=[name_pattern])
        self.analyzer.registry.add_recognizer(name_recognizer)

        # 4. Clinician & Signature Context Pattern at bottom of clinical notes (e.g. Clinician: Dr. K. Muwonge, Signed: KM)
        clinician_pattern = Pattern(
            name="clinician_staff_context",
            regex=r"(?i:(?:clinician|doctor|dr\.|physician|nurse|medical\s+officer|officer|prescriber|examiner|attended\s+by|examined\s+by|signed|signature))\s*[:=-]\s*([A-Za-z.'-\s]{2,30})",
            score=0.85
        )
        clinician_recognizer = PatternRecognizer(supported_entity="PERSON", patterns=[clinician_pattern])
        self.analyzer.registry.add_recognizer(clinician_recognizer)

        # 5. Localized Ugandan Contact / Phone Pattern (e.g. +256 701 449 283, 0772 123 456, Phone: ...)
        phone_pattern = Pattern(
            name="ugandan_phone_pattern",
            regex=r"(?i:(?:phone|tel|mobile|contact|cell)?\s*[:=-]?\s*)?(?:\+?256[-.\s]?|0)[37]\d{2}[-.\s]?\d{3}[-.\s]?\d{3}",
            score=0.9
        )
        phone_recognizer = PatternRecognizer(supported_entity="PHONE_NUMBER", patterns=[phone_pattern])
        self.analyzer.registry.add_recognizer(phone_recognizer)

        # 6. Professional & Honorific Title Person Pattern (e.g. Dr. K. Muwonge, Nurse Nakato, Dr. Okello)
        title_pattern = Pattern(
            name="title_person_pattern",
            regex=r"\b(?:Dr\.|Doctor|Nurse|Sister|Prof\.|Mr\.|Mrs\.|Ms\.)\s+[A-Z][a-zA-Z.'-]*(?:\s+[A-Z][a-zA-Z.'-]*)*\b",
            score=0.9
        )
        title_recognizer = PatternRecognizer(supported_entity="PERSON", patterns=[title_pattern])
        self.analyzer.registry.add_recognizer(title_recognizer)

    def _is_facility_name(self, text_segment: str) -> bool:
        """Determines if a matched text segment represents a hospital/facility name."""
        low = text_segment.lower()
        return any(kw in low for kw in FACILITY_EXCLUSION_KEYWORDS)

    def anonymize_text(self, text: str) -> Dict[str, Any]:
        """
        Analyzes and masks patient info, contacts, and IDs from raw clinical text.
        Explicitly excludes and preserves hospital/facility names.
        """
        if not text:
            return {"anonymized_text": "", "detected_entities": []}

        detected_entities = []

        if self.analyzer and self.anonymizer:
            try:
                target_entities = ["PERSON", "MEDICAL_RECORD_NUMBER", "NATIONAL_ID", "PHONE_NUMBER", "EMAIL_ADDRESS"]
                raw_results = self.analyzer.analyze(text=text, entities=target_entities, language="en")

                # Filter out facility names and invalid spans
                filtered_results = []
                for r in raw_results:
                    matched_text = text[r.start:r.end]
                    if not self._is_facility_name(matched_text):
                        filtered_results.append(r)
                        detected_entities.append({
                            "entity_type": r.entity_type,
                            "match": matched_text,
                            "start": r.start,
                            "end": r.end,
                            "score": r.score
                        })

                if filtered_results:
                    anonymized = self.anonymizer.anonymize(
                        text=text,
                        analyzer_results=filtered_results,
                        operators={
                            "PERSON": OperatorConfig("replace", {"new_value": "<REDACTED_PATIENT_NAME>"}),
                            "MEDICAL_RECORD_NUMBER": OperatorConfig("replace", {"new_value": "<REDACTED_MRN>"}),
                            "NATIONAL_ID": OperatorConfig("replace", {"new_value": "<REDACTED_NIN>"}),
                            "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "<REDACTED_PHONE>"}),
                            "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<REDACTED_EMAIL>"}),
                            "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"})
                        }
                    )
                    return {
                        "anonymized_text": anonymized.text,
                        "detected_entities": detected_entities
                    }
            except Exception:
                pass

        # Offline regex-based detection
        clean_text = text
        detected = []

        # 1. Patient Names with contextual labels
        name_ctx_pattern = re.compile(
            r"((?:patient(?:'s)?(?:\s+(?:full|last|first)?\s*name)?|pt(?:\s+name)?|client(?:\s+name)?|first\s+name|last\s+name|next\s+of\s+kin|nok)\s*[:=-]\s*)([A-Za-z'-]{2,20})",
            re.IGNORECASE | re.MULTILINE
        )
        def replace_name(match):
            prefix = match.group(1)
            name_val = match.group(2).strip()
            if self._is_facility_name(name_val):
                return match.group(0)
            detected.append({"entity_type": "PERSON", "match": name_val})
            return f"{prefix}<REDACTED_PATIENT_NAME>"

        clean_text = name_ctx_pattern.sub(replace_name, clean_text)

        # 2. Clinician & Staff Signatures at bottom of notes
        staff_ctx_pattern = re.compile(
            r"((?:clinician|doctor|dr\.|physician|nurse|medical\s+officer|officer|prescriber|examiner|attended\s+by|examined\s+by|signed|signature)\s*[:=-]\s*)([A-Za-z.'-\s]{2,30})",
            re.IGNORECASE | re.MULTILINE
        )
        def replace_staff(match):
            prefix = match.group(1)
            name_val = match.group(2).strip()
            if self._is_facility_name(name_val):
                return match.group(0)
            detected.append({"entity_type": "PERSON", "match": name_val})
            return f"{prefix}<REDACTED_CLINICIAN>"

        clean_text = staff_ctx_pattern.sub(replace_staff, clean_text)

        # 3. Common titles (Dr., Doctor, Mr., Mrs., Ms., Miss)
        title_name_pattern = re.compile(r"\b(?:Dr\.|Doctor|Mr\.|Mrs\.|Ms\.|Miss|Nurse|Sister)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
        for m in title_name_pattern.finditer(clean_text):
            val = m.group(0)
            if not self._is_facility_name(val):
                clean_text = clean_text.replace(val, "<REDACTED_PATIENT_NAME>")
                detected.append({"entity_type": "PERSON", "match": val})

        # 4. Patient MRNs (Full match e.g. MRN-UG-482910, OPD-12345)
        mrn_matches = re.finditer(r"\b(?:MRN[-_\s]?[A-Z0-9]{2,8}[-_\s]?\d{3,8}|(?:OPD|IPD)[-_\s#:]*\d{4,8})\b", clean_text, re.IGNORECASE)
        for m in mrn_matches:
            val = m.group(0)
            if not self._is_facility_name(val):
                clean_text = clean_text.replace(val, "<REDACTED_MRN>")
                detected.append({"entity_type": "MEDICAL_RECORD_NUMBER", "match": val})

        # 5. National IDs (NIN)
        nin_matches = re.finditer(r"\b(?:NIN[:\s-]*)?(?:[A-Z]{2}\d{7}[A-Z]{4}|(?:CM|CF)[\s-]?\d{8}[\s-]?[A-Z0-9]{4})\b", clean_text)
        for m in nin_matches:
            val = m.group(0)
            clean_text = clean_text.replace(val, "<REDACTED_NIN>")
            detected.append({"entity_type": "NATIONAL_ID", "match": val})

        # 5. Contacts (Phone numbers)
        phone_matches = re.finditer(r"(?:\+?256[-.\s]?|0)\d{3}[-.\s]?\d{3}[-.\s]?\d{3}|(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", clean_text)
        for m in phone_matches:
            val = m.group(0)
            clean_text = clean_text.replace(val, "<REDACTED_PHONE>")
            detected.append({"entity_type": "PHONE_NUMBER", "match": val})

        # 6. Emails
        email_matches = re.finditer(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", clean_text)
        for m in email_matches:
            val = m.group(0)
            clean_text = clean_text.replace(val, "<REDACTED_EMAIL>")
            detected.append({"entity_type": "EMAIL_ADDRESS", "match": val})

        return {
            "anonymized_text": clean_text,
            "detected_entities": detected
        }
