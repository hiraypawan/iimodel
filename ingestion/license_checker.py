"""Conservative root-license detection for repository ingestion.

This is a text-pattern filter, not legal advice or a full SPDX expression
parser. Ambiguous, dual, missing, or unrecognized licenses are rejected for
automatic ingestion so a human can review them.
"""

from __future__ import annotations

import re
from pathlib import Path

LICENSE_FILENAMES = (
    "LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENCE.txt",
    "COPYING", "COPYING.txt",
)

def check_license(repo_path: str | Path) -> tuple[bool, str, str]:
    """Return ``(is_allowed, license_type, reason)`` from a root license file."""
    root = Path(repo_path)
    license_path = next(
        (root / name for name in LICENSE_FILENAMES if (root / name).is_file()),
        None,
    )
    if license_path is None:
        return False, "NOASSERTION", "No recognized root LICENSE/COPYING file was found."
    try:
        text = license_path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except OSError as exc:
        return False, "NOASSERTION", f"Could not read {license_path.name}: {exc}"

    raw_upper = text.upper()
    spdx_lines = re.findall(
        r"SPDX-LICENSE-IDENTIFIER\s*:\s*([^\r\n]+)", raw_upper
    )
    upper = re.sub(r"[\s_]+", " ", raw_upper)
    if any(re.search(r"\b(?:A?GPL|GPL)-?\d|\bGNU GENERAL PUBLIC LICENSE\b", item) for item in spdx_lines):
        return False, "GPL", "Copyleft license detected; automatic ingestion is disabled."
    if len(spdx_lines) > 1:
        return False, "AMBIGUOUS", "Multiple SPDX declarations require manual review."
    spdx_map = {
        "MIT": "MIT",
        "APACHE-2.0": "Apache-2.0",
        "BSD-2-CLAUSE": "BSD-2-Clause",
        "BSD-3-CLAUSE": "BSD-3-Clause",
    }
    for line in spdx_lines:
        identifier = line.strip().strip(".;")
        if identifier in spdx_map:
            accepted = spdx_map[identifier]
            return True, accepted, f"Recognized SPDX identifier in {license_path.name}."
        if identifier:
            return False, "AMBIGUOUS", f"SPDX expression needs manual review: {identifier}."

    if "GNU GENERAL PUBLIC LICENSE" in upper or re.search(r"\b(?:A?GPL|GPL)-?\d", upper):
        return False, "GPL", "Copyleft license text detected; automatic ingestion is disabled."
    # Detect a small set of common license texts. Multiple signatures in one
    # file (for example, a dual-license offer) are left for human review.
    has_apache = "APACHE LICENSE" in upper and "VERSION 2.0" in upper
    has_mit = "MIT LICENSE" in upper or (
        "PERMISSION IS HEREBY GRANTED, FREE OF CHARGE" in upper
        and "THE SOFTWARE IS PROVIDED" in upper
    )
    has_bsd_grant = (
        "REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS" in upper
        and "ARE PERMITTED PROVIDED THAT" in upper
        and "AS IS" in upper
    )
    has_bsd3 = "BSD 3-CLAUSE" in upper or "BSD 3 CLAUSE" in upper
    has_bsd2 = "BSD 2-CLAUSE" in upper or "BSD 2 CLAUSE" in upper
    if has_bsd_grant:
        if "NEITHER THE NAME" in upper:
            has_bsd3 = True
        else:
            has_bsd2 = True

    detected = [
        ("Apache-2.0", has_apache),
        ("MIT", has_mit),
        ("BSD-2-Clause", has_bsd2),
        ("BSD-3-Clause", has_bsd3),
    ]
    matches = [license_name for license_name, found in detected if found]
    if len(matches) == 1:
        license_name = matches[0]
        return True, license_name, f"Recognized {license_name} text in {license_path.name}."
    if len(matches) > 1:
        return False, "AMBIGUOUS", "Multiple license signatures require manual review."
    return False, "UNRECOGNIZED", f"Could not confidently identify an allowed license in {license_path.name}."
