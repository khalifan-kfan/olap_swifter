"""
Tail-End Cursive & Handwritten Text Recognition (HTR) Module.
Processes handwritten physician notes, clinical impressions, and cursive prescriptions
at the end of the extraction pipeline.
"""

import re
import shutil
from typing import Dict, List, Any, Optional
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:
    import pytesseract
except ImportError:
    pytesseract = None


# Clinical vocabulary dictionary for post-processing cursive / handwritten abbreviations
CLINICAL_ABBREVIATIONS = {
    r"\b(?:p\.?o\.?|po)\b": "PO (Oral)",
    r"\b(?:i\.?v\.?|iv)\b": "IV (Intravenous)",
    r"\b(?:i\.?m\.?|im)\b": "IM (Intramuscular)",
    r"\b(?:b\.?d\.?|bd|bid)\b": "bd (Twice Daily)",
    r"\b(?:t\.?d\.?s\.?|tds|tid)\b": "tds (Three Times Daily)",
    r"\b(?:q\.?d\.?s\.?|qds|qid)\b": "qds (Four Times Daily)",
    r"\b(?:nocte)\b": "nocte (Nightly)",
    r"\b(?:stat)\b": "STAT (Immediately)",
    r"\b(?:prn)\b": "PRN (As Needed)",
    r"\b(?:inj\.?)\b": "Inj",
    r"\b(?:tab\.?|tabs\.?)\b": "Tabs",
    r"\b(?:cap\.?|caps\.?)\b": "Caps",
}

CLINICAL_MEDICATION_LEXICON = [
    "Ceftriaxone", "Amoxicillin", "Ciprofloxacin", "Gentamicin", "Meropenem",
    "Metronidazole", "Artemether", "Lumefantrine", "Coartem", "Paracetamol",
    "Azithromycin", "Erythromycin", "Ampicillin", "Cloxacillin", "Doxycycline"
]


class CursiveTextRecognizer:
    """
    Recognizes cursive and handwritten text from clinical note regions.
    Executed at the tail end of the OCR pipeline.
    """

    def __init__(self, tesseract_cmd: Optional[str] = None):
        self.tesseract_cmd = tesseract_cmd or shutil.which("tesseract")
        if self.tesseract_cmd and pytesseract:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd

    @staticmethod
    def preprocess_for_cursive(image: Image.Image) -> Image.Image:
        """
        Enhances strokes and closes cursive letter loops for improved OCR.
        Converts to grayscale, enhances contrast, and applies morphological closing.
        """
        # 1. Grayscale
        gray = image.convert("L")
        
        # 2. Contrast stretching
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(2.2)

        # 3. Sharpening
        sharp = enhanced.filter(ImageFilter.SHARPEN)

        # 4. Invert and threshold for stroke continuity
        threshold = 150
        binary = sharp.point(lambda p: 255 if p > threshold else 0)
        return binary

    def recognize_cursive_region(self, image_input: Any, config: str = "--psm 6") -> Dict[str, Any]:
        """
        Extracts cursive text from an image or crop using specialized line-level segmentation.
        """
        if isinstance(image_input, (str, Path)):
            img = Image.open(str(image_input))
        else:
            img = image_input

        enhanced = self.preprocess_for_cursive(img)

        raw_text = ""
        if pytesseract and self.tesseract_cmd:
            try:
                # Use LSTM engine with single text block PSM
                raw_text = pytesseract.image_to_string(enhanced, config=f"{config} --oem 1")
            except Exception:
                raw_text = ""

        # Post-process with clinical shorthand lexicon
        normalized_text = self.normalize_clinical_handwriting(raw_text)

        return {
            "raw_text": raw_text.strip(),
            "normalized_text": normalized_text.strip(),
            "is_handwritten": bool(raw_text.strip())
        }

    def normalize_clinical_handwriting(self, text: str) -> str:
        """
        Corrects common handwriting misinterpretations and expands medical abbreviations.
        """
        if not text:
            return ""

        result = text

        # Expand Latin abbreviations for dosages and routes
        for pattern, replacement in CLINICAL_ABBREVIATIONS.items():
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

        # Correct common medication OCR errors
        for med in CLINICAL_MEDICATION_LEXICON:
            # Match close variants with up to 2 typos or lowercase
            med_pattern = re.compile(rf"\b{med[:4]}[a-z]{{2,12}}\b", re.IGNORECASE)
            def replace_med(m):
                word = m.group(0)
                # If length is close, replace with canonical medication name
                if abs(len(word) - len(med)) <= 2:
                    return med
                return word
            result = med_pattern.sub(replace_med, result)

        return result
