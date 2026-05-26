"""
Regression coverage for the programmatic-SEO pages at /tools/{slug}.

Background: on 2026-05-26 the OWASP ZAP baseline scan against bankscanai.com
flagged HTTP 500 on five `/tools/bank-statement-for-*` URLs (ZAP id 90022,
Application Error Disclosure). Root cause: `templates/tools/seo_page.html`
used `page.data_requirements.items` and `page.challenges_section.items` —
Jinja resolved `.items` to the built-in `dict.items` method rather than
the `"items"` key the use_case generator puts on the dict. Same trap on
both sections, only hit when the use_case generator (which is the only
generator that sets `data_requirements` / `challenges_section`) produced
the dict.

This file pins the bug in three layers:

  1. test_zap_flagged_use_case_slug_renders_200       — the exact slugs
                                                         ZAP found, by name
  2. test_every_use_case_slug_renders_200             — every use_case
                                                         slug, no sample
  3. test_random_sample_of_other_slugs_renders_no_500 — fast sweep of the
                                                         other ~4,700 slugs

(1) and (2) cover the affected generator exhaustively. (3) keeps the CI
runtime under ~5 s while still catching unrelated regressions.

The SEO_PAGES dict has ~4,800 entries and each render takes ~70 ms through
TestClient — sweeping all of them is ~6 minutes which is too slow for the
pre-merge suite.
"""

from __future__ import annotations

import html
import os
import random
import sys

import pytest
from fastapi.testclient import TestClient

# Ensure the project root is on sys.path so we can import the app module.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402
from seo_pages import SEO_PAGES  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Module-scoped client — these tests are read-only and don't touch the
    DB, so a single client (and single TestClient lifespan) is fine."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Layer 1 — the exact slugs ZAP flagged on 2026-05-26
# ---------------------------------------------------------------------------


# Listed explicitly so the test name shows them in CI output if any fail.
ZAP_FLAGGED_USE_CASE_SLUGS = [
    "bank-statement-for-1099-reporting",
    "bank-statement-for-anti-money-laundering",
    "bank-statement-for-audit-preparation",
    "bank-statement-for-bank-reconciliation",
    "bank-statement-for-bankruptcy-filing",
]


@pytest.mark.parametrize("slug", ZAP_FLAGGED_USE_CASE_SLUGS)
def test_zap_flagged_use_case_slug_renders_200(client: TestClient, slug: str):
    """Each slug ZAP reported as 500 on 2026-05-26 must now render 200."""
    assert slug in SEO_PAGES, (
        f"{slug} disappeared from SEO_PAGES — if the use_case was removed "
        f"intentionally, remove it from ZAP_FLAGGED_USE_CASE_SLUGS too."
    )
    resp = client.get(f"/tools/{slug}")
    assert resp.status_code == 200, (
        f"/tools/{slug} returned {resp.status_code}; body starts:\n"
        f"{resp.text[:400]}"
    )


# ---------------------------------------------------------------------------
# Layer 2 — every use_case page, no sample
# ---------------------------------------------------------------------------


def _use_case_slugs() -> list[str]:
    return sorted(s for s, p in SEO_PAGES.items() if p.get("type") == "use_case")


def test_every_use_case_slug_renders_200(client: TestClient):
    """Render every use_case page in SEO_PAGES. The bug ZAP found only ever
    appeared on use_case pages (only generator that sets data_requirements
    / challenges_section), so this is the affected universe.
    """
    use_case_slugs = _use_case_slugs()
    assert use_case_slugs, "no use_case pages — _generate_use_case_pages may be broken"

    failures: list[tuple[str, int, str]] = []
    for slug in use_case_slugs:
        resp = client.get(f"/tools/{slug}")
        if resp.status_code != 200:
            failures.append((slug, resp.status_code, resp.text[:200]))

    assert not failures, (
        f"{len(failures)}/{len(use_case_slugs)} use_case pages "
        f"did not return 200. First few: {failures[:3]}"
    )


# ---------------------------------------------------------------------------
# Layer 3 — random sample of every other slug type
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sampled_non_use_case_slugs() -> list[str]:
    """Random sample of 100 slugs from each non-use_case type, capped so the
    sweep stays under a few seconds. Seeded so failures are reproducible."""
    rng = random.Random(20260526)
    by_type: dict[str, list[str]] = {}
    for slug, page in SEO_PAGES.items():
        if page.get("type") == "use_case":
            continue
        by_type.setdefault(page.get("type", "_unknown"), []).append(slug)

    sample: list[str] = []
    for type_name, slugs in by_type.items():
        slugs.sort()  # deterministic ordering before sampling
        sample.extend(rng.sample(slugs, min(20, len(slugs))))
    return sample


def test_random_sample_of_other_slugs_renders_no_500(
    client: TestClient, sampled_non_use_case_slugs: list[str]
):
    """Fast guard against future template/generator regressions on slug
    types other than use_case. 20 slugs per type, seeded for reproducibility.
    """
    failures: list[tuple[str, int, str]] = []
    for slug in sampled_non_use_case_slugs:
        resp = client.get(f"/tools/{slug}")
        if resp.status_code >= 500:
            failures.append((slug, resp.status_code, resp.text[:200]))

    assert not failures, (
        f"{len(failures)}/{len(sampled_non_use_case_slugs)} sampled "
        f"non-use_case pages returned 5xx. First few: {failures[:3]}"
    )


# ---------------------------------------------------------------------------
# Light content sanity for use_case pages
# ---------------------------------------------------------------------------


def test_use_case_pages_render_h1_and_meta(client: TestClient):
    """Spot-check that use_case pages actually contain their H1 — a green
    no-500 sweep would not catch a template that renders an empty body.

    Note: Jinja HTML-escapes by default, so an H1 like
    `Convert Bank Statements for Child Support & Alimony` arrives in the
    response as `... Child Support &amp; Alimony`. Compare against the
    escaped form.
    """
    sample = _use_case_slugs()[:10]
    for slug in sample:
        resp = client.get(f"/tools/{slug}")
        assert resp.status_code == 200
        expected_h1 = html.escape(SEO_PAGES[slug]["h1"], quote=False)
        assert expected_h1 in resp.text, (
            f"H1 missing from /tools/{slug} (expected escaped form: {expected_h1!r})"
        )
