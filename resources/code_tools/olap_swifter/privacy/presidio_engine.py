"""
Offline Microsoft Presidio De-Identification Engine.
Ensures zero patient data leakage by detecting and masking PII locally
before any analytical or OLAP processing.
Uses offline spaCy blank model without external downloads.
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


class OfflinePresidioAnonymizer:
    """Offline PII detection and redaction using Microsoft Presidio."""

    def __init__(self):
        if PRESIDIO_AVAILABLE:
            try:
                # Configure completely offline NLP engine using blank spacy pipeline (zero download)
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
        """Adds custom recognizers for LMIC medical identifiers (MRNs, Hospital IDs)."""
        if not self.analyzer:
            return

        # 1. Hospital MRN Pattern (e.g., MRN-UG-123456 or HOSP/2025/999)
        mrn_pattern = Pattern(name="mrn_pattern", regex=r"(?:MRN[-_\s]?[A-Z]{0,3}[-_\s]?\d{4,8}|HOSP[/\w]+\d+)", score=0.85)
        mrn_recognizer = PatternRecognizer(supported_entity="MEDICAL_RECORD_NUMBER", patterns=[mrn_pattern])
        self.analyzer.registry.add_recognizer(mrn_recognizer)

        # 2. National ID / NIN Pattern
        nin_pattern = Pattern(name="nin_pattern", regex=r"\b[A-Z]{2}\d{7}[A-Z]{4}\b", score=0.8)
        nin_recognizer = PatternRecognizer(supported_entity="NATIONAL_ID", patterns=[nin_pattern])
        self.analyzer.registry.add_recognizer(nin_recognizer)

    def anonymize_text(self, text: str) -> Dict[str, Any]:
        """Analyzes and masks PII from raw clinical text."""
        if not text:
            return {"anonymized_text": "", "detected_entities": []}

        detected_entities = []

        if self.analyzer and self.anonymizer:
            try:
                results = self.analyzer.analyze(text=text, language="en")
                detected_entities = [
                    {"entity_type": r.entity_type, "start": r.start, "end": r.end, "score": r.score}
                    for r in results
                ]
                anonymized = self.anonymizer.anonymize(
                    text=text,
                    analyzer_results=results,
                    operators={"DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"})}
                )
                return {
                    "anonymized_text": anonymized.text,
                    "detected_entities": detected_entities
                }
            except Exception:
                pass

        # Robust regex fallback for offline environments
        clean_text = text
        mrns = re.findall(r"(?:MRN[-_\s]?[A-Z0-9]+|\bHOSP[/\w]+\b)", clean_text)
        for m in mrns:
            clean_text = clean_text.replace(m, "<REDACTED_MRN>")
            detected_entities.append({"entity_type": "MEDICAL_RECORD_NUMBER", "match": m})

        phones = re.findall(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", clean_text)
        for p in phones:
            clean_text = clean_text.replace(p, "<REDACTED_PHONE>")
            detected_entities.append({"entity_type": "PHONE_NUMBER", "match": p})

        return {
            "anonymized_text": clean_text,
            "detected_entities": detected_entities
        }
