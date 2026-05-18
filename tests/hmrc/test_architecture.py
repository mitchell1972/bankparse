"""
Architecture guard tests for the `hmrc/` module.

These fail fast if someone (including future-me) starts piling business
logic back into the routers, or duplicates HMRC category constants outside
the canonical schema. The goal is to keep the module honest as it grows.

If one of these starts failing, the fix is almost always to MOVE code to
the right layer, not to bump the threshold.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HMRC_DIR = Path(__file__).resolve().parents[2] / "hmrc"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _iter_python(dirname: str):
    """Yield (path, source) pairs for every .py file under hmrc/<dirname>/."""
    folder = HMRC_DIR / dirname
    if not folder.exists():
        return
    for p in folder.iterdir():
        if p.suffix == ".py" and p.name != "__init__.py":
            yield p, _read(p)


# ---------------------------------------------------------------------------
# 1. Routers stay thin
# ---------------------------------------------------------------------------

# Hard cap on lines per router file. Routers should be: validate request,
# call a service, return the result. ~150 lines is generous. If we need
# more, the right move is to extract another service, not to grow the file.
_ROUTER_MAX_LINES = 150


def test_routers_stay_thin():
    """Each HMRC router file is bounded so business logic can't sneak back in."""
    offenders = []
    for path, src in _iter_python("routers"):
        loc = sum(1 for line in src.splitlines() if line.strip())
        if loc > _ROUTER_MAX_LINES:
            offenders.append(f"{path.relative_to(HMRC_DIR)}: {loc} non-blank lines")
    assert not offenders, (
        "HMRC routers are exceeding the line cap — move logic to a service:\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + f"\n(cap: {_ROUTER_MAX_LINES} non-blank lines per router)"
    )


# ---------------------------------------------------------------------------
# 2. Routers must not touch the database directly
# ---------------------------------------------------------------------------

_DB_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+database\s+import|import\s+database)\b", re.MULTILINE,
)


def test_routers_do_not_import_database_directly():
    """Routers go through repositories. If a router imports `database`,
    it's about to do SQL where SQL doesn't belong."""
    offenders = []
    for path, src in _iter_python("routers"):
        if _DB_IMPORT_RE.search(src):
            offenders.append(str(path.relative_to(HMRC_DIR)))
    assert not offenders, (
        "These HMRC routers import `database` directly — move the SQL to a "
        "repository under hmrc/repositories/ instead:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# 3. HMRC category names must come from the canonical schema
# ---------------------------------------------------------------------------

_CANONICAL = HMRC_DIR / "schemas" / "categories.py"

# String literals that look like HMRC category API values. We catch the
# distinctive camelCase HMRC field names; anything generic like "other" is
# excluded because it's also a Python keyword-y word that appears in docs.
_HMRC_CATEGORY_LITERAL_RE = re.compile(
    r'"(turnover|otherIncome|costOfGoodsBought|cisPaymentsToSubcontractors|'
    r'staffCosts|travelCosts|premisesRunningCosts|maintenanceCosts|'
    r'adminCosts|advertisingCosts|businessEntertainmentCosts|interest|'
    r'financialCharges|badDebt|professionalFees|depreciation|'
    r'rentIncome|premiumsOfLeaseGrant|repairsAndMaintenance|'
    r'financialCosts|costOfServices|residentialFinancialCost)"'
)


def test_category_names_only_defined_in_canonical_schema():
    """No file outside `hmrc/schemas/categories.py` may hardcode an HMRC
    category string — they must import the constant.

    Test files are allowed because they're often asserting on the wire value.
    """
    project_root = HMRC_DIR.parent
    offenders: list[str] = []
    for path in project_root.rglob("*.py"):
        # skip __pycache__, the canonical file itself, the test that defines
        # the regex, and anything inside tests/ (test code asserts on real
        # wire values, that's the point).
        if "__pycache__" in path.parts:
            continue
        if path == _CANONICAL:
            continue
        if path == Path(__file__):
            continue
        if "tests" in path.parts:
            continue
        # Skip the .claude worktree noise + other vendor dirs.
        if ".claude" in path.parts and path.resolve() != Path(__file__).resolve().parent.parent.parent.joinpath(path.name).resolve():
            # We ARE inside .claude/worktrees in dev — this guard would skip
            # everything. Allow when we're already running from there.
            pass
        src = _read(path)
        if _HMRC_CATEGORY_LITERAL_RE.search(src):
            offenders.append(str(path.relative_to(project_root)))
    assert not offenders, (
        "HMRC category strings are hardcoded outside the canonical schema — "
        "import the constant from hmrc.schemas.categories instead:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# 4. Services don't import FastAPI / Request
# ---------------------------------------------------------------------------

_FASTAPI_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+fastapi(?:\.|\s)|import\s+fastapi)\b", re.MULTILINE,
)


def test_services_do_not_import_fastapi():
    """Services must be pure orchestration — callable from a CLI, a cron
    job, or a unit test without spinning up FastAPI."""
    offenders = []
    for path, src in _iter_python("services"):
        if _FASTAPI_IMPORT_RE.search(src):
            offenders.append(str(path.relative_to(HMRC_DIR)))
    assert not offenders, (
        "These HMRC services import FastAPI — keep HTTP concerns in the "
        "router, pass plain data into the service:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )
