from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from blackboard.component_catalog.validate_catalog import (
    source_integrity_errors,
    validate_catalog,
    validate_selection,
)


CATALOG_DIR = Path(__file__).resolve().parents[1] / "blackboard" / "component_catalog"


def approved_component() -> dict:
    return {
        "id": "ui.button.primary.v1",
        "kind": "component",
        "name": "Primary Button",
        "summary": "A button used for a primary action.",
        "tags": ["button", "primary"],
        "status": "approved",
        "target": {"framework": "react", "language": "tsx", "styling": "tailwind"},
        "source": {
            "url": "https://example.org/repo",
            "revision": "test-revision",
            "path": "src/Button.tsx",
            "content_sha256": "a" * 64,
        },
        "license": {
            "spdx_id": "MIT",
            "license_url": "https://example.org/license",
            "review_status": "verified",
            "reviewed_by": "test",
            "reviewed_at": "2026-10-18T00:00:00Z",
            "notice_required": True,
        },
        "dependencies": [],
        "entrypoint": "src/Button.tsx",
        "export_name": "PrimaryButton",
        "props_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
        },
        "slots": {},
        "validation": {
            "build": "pass",
            "unit_tests": "pass",
            "accessibility": "pass",
            "visual_review": "accepted",
        },
    }


class ComponentCatalogTests(unittest.TestCase):
    def test_checked_in_candidate_catalog_is_valid_and_hashes_sources(self):
        catalog = json.loads((CATALOG_DIR / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["components"]), 10)
        self.assertTrue(all(item["status"] == "candidate" for item in catalog["components"]))
        self.assertEqual(validate_catalog(catalog, check_sources=True), [])

    def test_source_hash_validation_detects_modified_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "component.html"
            asset.write_text("original", encoding="utf-8")
            catalog = {"components": [{"source": {"path": "component.html", "content_sha256": "0" * 64}}]}
            self.assertTrue(source_integrity_errors(catalog, root))

    def test_approved_component_needs_license_and_validation_gates(self):
        schema = json.loads((CATALOG_DIR / "catalog.schema.json").read_text(encoding="utf-8"))
        catalog = {"schema_version": "1.0", "components": [approved_component()]}
        self.assertEqual(validate_catalog(catalog, schema), [])
        catalog["components"][0]["license"]["review_status"] = "pending"
        self.assertTrue(validate_catalog(catalog, schema))
        catalog["components"][0]["license"]["review_status"] = "verified"
        del catalog["components"][0]["license"]["reviewed_by"]
        self.assertTrue(validate_catalog(catalog, schema))

    def test_selection_checks_approved_ids_and_props(self):
        schema = json.loads((CATALOG_DIR / "catalog.schema.json").read_text(encoding="utf-8"))
        catalog = {"schema_version": "1.0", "components": [approved_component()]}
        selection = {
            "schema_version": "1.0",
            "target_id": "react-ts-v1",
            "layout_id": "centered-card-v1",
            "theme": "dark",
            "regions": [
                {
                    "region_id": "main",
                    "component_id": "ui.button.primary.v1",
                    "props": {"label": "Continue"},
                }
            ],
        }
        self.assertEqual(validate_selection(selection, catalog), [])
        selection["regions"][0]["props"] = {"unknown": "value"}
        self.assertTrue(validate_selection(selection, catalog))
        selection["regions"][0]["component_id"] = "missing.component.v1"
        self.assertTrue(validate_selection(selection, catalog))


if __name__ == "__main__":
    unittest.main(verbosity=2)
