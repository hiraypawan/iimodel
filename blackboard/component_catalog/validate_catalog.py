"""Validate component-catalog metadata and model selection specs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
CATALOG_SCHEMA_PATH = HERE / "catalog.schema.json"
SELECTION_SCHEMA_PATH = HERE / "selection.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def validation_errors(instance: dict, schema: dict) -> list[str]:
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        return [f"Invalid JSON Schema: {exc}"]
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        error.message
        for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    ]


def source_integrity_errors(
    catalog: dict,
    project_root: str | Path = PROJECT_ROOT,
) -> list[str]:
    """Check local component asset paths and their recorded SHA-256 values."""
    root = Path(project_root).resolve()
    errors: list[str] = []
    for index, component in enumerate(catalog.get("components", [])):
        if not isinstance(component, dict):
            continue
        source = component.get("source", {})
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            continue
        candidate = (root / source["path"]).resolve()
        if candidate != root and root not in candidate.parents:
            errors.append(f"components[{index}] source path escapes the project root.")
            continue
        if not candidate.is_file():
            errors.append(f"components[{index}] source file is missing: {source['path']}.")
            continue
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual.lower() != str(source.get("content_sha256", "")).lower():
            errors.append(f"components[{index}] source SHA-256 does not match.")
    return errors


def validate_catalog(
    catalog: dict,
    schema: dict | None = None,
    *,
    check_sources: bool = False,
    project_root: str | Path = PROJECT_ROOT,
) -> list[str]:
    schema = schema or _load_json(CATALOG_SCHEMA_PATH)
    errors = validation_errors(catalog, schema)
    ids = [item.get("id") for item in catalog.get("components", []) if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("Component IDs must be unique.")
    if check_sources:
        errors.extend(source_integrity_errors(catalog, project_root=project_root))
    return errors


def validate_selection(
    selection: dict,
    catalog: dict,
    schema: dict | None = None,
) -> list[str]:
    schema = schema or _load_json(SELECTION_SCHEMA_PATH)
    errors = validation_errors(selection, schema)
    components = {
        item.get("id"): item for item in catalog.get("components", [])
        if isinstance(item, dict)
    }
    for index, region in enumerate(selection.get("regions", [])):
        if not isinstance(region, dict):
            continue
        component_id = region.get("component_id")
        component = components.get(component_id)
        if component is None:
            errors.append(f"regions[{index}] references unknown component {component_id!r}.")
            continue
        if component.get("status") != "approved":
            errors.append(f"regions[{index}] component {component_id!r} is not approved.")
            continue
        props_schema = component.get("props_schema")
        if isinstance(props_schema, dict):
            props_errors = validation_errors(region.get("props", {}), props_schema)
            errors.extend(
                f"regions[{index}] props: {message}" for message in props_errors
            )
    return errors


def main() -> int:
    catalog = _load_json(HERE / "catalog.json")
    errors = validate_catalog(catalog, check_sources=True)
    if errors:
        print("Catalog invalid:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Catalog metadata valid ({len(catalog['components'])} components).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
