"""
Experimental image/text similarity prototype.

The current implementation does not identify object regions or produce reliable
UI bounding boxes. It is intentionally not exposed as a supported app tool.
Optional transformers/torch dependencies are required for its image-level
similarity path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class VisionMatch:
    query: str
    bbox: tuple[float, float, float, float]  # x, y, w, h (normalized 0-1)
    confidence: float
    label: str


class MobileCLIPGrounder:
    """Optional image/text similarity prototype; it does not localize objects."""

    def __init__(self, model_name: str = "apple/mobileclip-v1"):
        self.model_name = model_name
        self.model = None
        self.processor = None
        self._available = False
        self._init_model()

    def _init_model(self):
        try:
            import torch  # type: ignore
            from transformers import AutoProcessor, AutoModel  # type: ignore
            self.processor = AutoProcessor.from_pretrained(self.model_name)
            self.model = AutoModel.from_pretrained(self.model_name)
            self.model.eval()
            self._available = True
        except ImportError:
            self._available = False
        except Exception:
            self._available = False

    def ground(
        self,
        image_path: str,
        query: str,
    ) -> Optional[VisionMatch]:
        """Return image-level similarity metadata, not a localized object box."""
        if not Path(image_path).is_file() or not self._available:
            return None

        try:
            from PIL import Image  # type: ignore
            import torch  # type: ignore

            image = Image.open(image_path).convert("RGB")
            inputs = self.processor(text=[query], images=image, return_tensors="pt")
            with torch.no_grad():
                outputs = self.model(**inputs)
            # A one-image/one-query score cannot identify a region or be
            # interpreted as a calibrated confidence. The full-image box is
            # retained only as a compatibility field for this prototype.
            similarity = float(outputs.logits_per_image[0, 0])
            return VisionMatch(
                query=query,
                bbox=(0.0, 0.0, 1.0, 1.0),
                confidence=similarity,
                label="whole-image similarity only",
            )
        except Exception:
            return None


def vision_ground_tool(image_path: str, query: str) -> str:
    """Return prototype status; this function does not locate UI regions."""
    grounder = MobileCLIPGrounder()
    match = grounder.ground(image_path, query)

    if match is None:
        if not Path(image_path).is_file():
            return f"Image file not found: {image_path}"
        return "Vision model unavailable; no visual result was produced."

    return (
        f"IMAGE-LEVEL SIMILARITY ONLY for '{match.query}' "
        f"(score={match.confidence:.3f}). The full-image compatibility box is not a localized UI region."
    )


if __name__ == "__main__":
    print("MobileCLIP grounder module loaded. Available:", MobileCLIPGrounder()._available)
