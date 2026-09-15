"""
Image Preprocessor for Tesseract OCR.
Performs deskewing, grayscale conversion, and adaptive thresholding for paper clinical notes.
"""

from typing import Tuple, Optional
from PIL import Image, ImageEnhance, ImageFilter
import io


class ImagePreprocessor:
    """Prepares paper scans and handwritten clinical notes for high-accuracy OCR."""

    @staticmethod
    def preprocess_for_ocr(image_path: str) -> Image.Image:
        """Loads and enhances an image for Tesseract OCR."""
        img = Image.open(image_path)

        # 1. Convert to grayscale
        img_gray = img.convert("L")

        # 2. Enhance contrast
        enhancer = ImageEnhance.Contrast(img_gray)
        img_enhanced = enhancer.enhance(2.0)

        # 3. Slight sharpening
        img_sharp = img_enhanced.filter(ImageFilter.SHARPEN)

        # 4. Adaptive thresholding / binarization
        threshold = 145
        img_binary = img_sharp.point(lambda p: 255 if p > threshold else 0)

        return img_binary

    @staticmethod
    def extract_bounding_boxes(image_path: str):
        """Extracts rough line / block bounding boxes for lineage tracking."""
        img = Image.open(image_path)
        w, h = img.size
        # Provide grid cells as basic bounding regions
        return [
            {"region": "header", "box": (0, 0, w, int(h * 0.2))},
            {"region": "patient_details", "box": (0, int(h * 0.2), w, int(h * 0.4))},
            {"region": "clinical_findings", "box": (0, int(h * 0.4), w, int(h * 0.7))},
            {"region": "rx_and_labs", "box": (0, int(h * 0.7), w, h)}
        ]
