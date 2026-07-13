#!/usr/bin/env python3
"""Validate a repo against ARTIFACT_STANDARD.md Tier 0. Exit 1 = push blocked."""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
REQUIRED_README_SECTIONS = ["## Problem", "## Solution", "## System", "## Outcome", "## Version Log"]
BANNED_WITHOUT_TRIGGER = ["SYSTEM_WALKTHROUGH.md", "CHANGELOG.md", "RUNBOOK.md",
                          "PRODUCTION_READINESS.md", "THREAT_MODEL.md", "MONITORING.md",
                          "INCIDENT_RESPONSE.md", "TEST_MATRIX.md",
                          "DEMO_SCRIPT.md", "ASSURANCE_ONE_PAGER.md",
                          "TECHNICAL_OWNERSHIP_GUIDE.md"]
# Tier 1 artifacts (ARTIFACT_STANDARD.md #Tier 1) are allowed without an ADR
# trigger only for the current flagship — exactly one at a time.
TIER1_ARTIFACTS = {"DEMO_SCRIPT.md", "ASSURANCE_ONE_PAGER.md", "TECHNICAL_OWNERSHIP_GUIDE.md"}
CURRENT_FLAGSHIP = "ai-reliability-engine"
IS_FLAGSHIP = ROOT.resolve().name == CURRENT_FLAGSHIP
errors = []

# GOVERNANCE.md §3, Build-repo STATE rule: STATE.md is part of the scaffold.
if not (ROOT / "STATE.md").exists():
    errors.append("STATE.md missing (GOVERNANCE section 3, Build-repo STATE rule)")

readme = ROOT / "README.md"
if not readme.exists():
    errors.append("README.md missing")
else:
    text = readme.read_text(encoding="utf-8")
    for section in REQUIRED_README_SECTIONS:
        if section not in text:
            errors.append(f"README missing section: {section}")

adr = ROOT / "adr"
if not adr.is_dir():
    errors.append("adr/ folder missing")
else:
    count = len([f for f in adr.glob("*.md") if "template" not in f.name.lower()])
    if count == 0:
        errors.append("adr/ has no decisions (need 1-5)")
    elif count > 5:
        errors.append(f"adr/ has {count} decisions (cap is 5 - decisions were not decisions)")

for banned in BANNED_WITHOUT_TRIGGER:
    if (ROOT / banned).exists():
        if banned in TIER1_ARTIFACTS and IS_FLAGSHIP:
            continue
        # allowed only if an ADR mentions it (the trigger record)
        justified = adr.is_dir() and any(
            re.search(re.escape(banned), f.read_text(encoding="utf-8"))
            for f in adr.glob("*.md"))
        if not justified:
            errors.append(f"{banned} exists without an ADR citing its trigger")

if errors:
    print("ARTIFACT_STANDARD violations:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("Tier 0: PASS")
