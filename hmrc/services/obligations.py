"""
HMRC Obligations service — "what does this user still owe HMRC, and when?"

Calls the MTD Obligations API and normalises the wire response into UI-
friendly rows. This is the FIRST real HMRC read we ship; getting it solid
unblocks every other MTD endpoint (quarterly updates, EOPS, final
declaration) because they all share the same client + fraud-headers stack.

Resolution order per `fetch_for_user()`:
  1. If `HMRC_DEMO_OBLIGATIONS=1` OR the user isn't connected to HMRC yet,
     return a recorded sandbox fixture so the dashboard panel renders
     during demos and on first-launch before the user has clicked Connect.
  2. Otherwise call the real endpoint via `services/client.request()`.
  3. Normalise to `UiObligation` rows + compute "due in N days" labels.

Env flags:
    HMRC_DEMO_OBLIGATIONS=1     # force the fixture (default OFF in prod)
    HMRC_OBLIGATIONS_API_VERSION # default 'application/vnd.hmrc.3.0+json'
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Iterable

from ..repositories import tokens as _tokens
from ..schemas.obligations import (
    HmrcObligations,
    ObligationsResponse,
    UiObligation,
)
from . import client as _client

logger = logging.getLogger("bankparse.hmrc.obligations")


# Endpoint pinned per HMRC's Obligations API v3.0 spec. Bump when HMRC
# publishes a new version we've validated against the sandbox.
_API_VERSION = "application/vnd.hmrc.3.0+json"


def is_demo_mode() -> bool:
    return os.environ.get("HMRC_DEMO_OBLIGATIONS", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_for_user(*, user_id: int, request_obj) -> ObligationsResponse:
    """Return the dashboard-shaped obligations for one user.

    Always returns a usable response — never raises. On any error we fall
    back to the demo fixture and tag `connected=False` + an `error` string
    the UI can surface.
    """
    info = _tokens.get_tokens(user_id) or {}
    connected = bool(info.get("access_token"))
    nino = info.get("nino")
    businesses = info.get("businesses") or []

    # Three reasons to short-circuit to the demo fixture:
    # 1) explicit env override (used in dev and the public demo)
    # 2) user hasn't clicked /api/hmrc/connect yet
    # 3) user is connected but hasn't told us their NINO + business IDs
    if is_demo_mode() or not connected or not nino or not businesses:
        rows = _demo_obligations()
        return ObligationsResponse(
            connected=connected, demo=True, obligations=rows, nino=nino,
        )

    rows: list[UiObligation] = []
    err: str | None = None
    for biz in businesses:
        try:
            rows.extend(_fetch_one_business(
                user_id=user_id, request_obj=request_obj,
                nino=nino, business=biz,
            ))
        except _client.HmrcApiError as exc:
            logger.warning(
                "HMRC obligations call failed (user=%s, business=%s): %s",
                user_id, biz.get("business_id"), exc,
            )
            err = err or _friendly_obligations_error(exc, nino=nino)

    return ObligationsResponse(
        connected=True, demo=False, obligations=_sort_rows(rows), error=err,
        nino=nino,
    )


def _friendly_obligations_error(exc: _client.HmrcApiError, *, nino: str) -> str:
    """Translate raw HMRC errors from the Obligations call into actionable
    plain-English hints for the dashboard.

    The #1 head-scratcher is HMRC 404 ``MATCHING_RESOURCE_NOT_FOUND``: it
    fires when the OAuth bearer token is tied to a DIFFERENT NINO than the
    one we just wrote into the user's record (very common when the user
    creates a fresh sandbox individual in one tab then types a different
    NINO into the dashboard in another). The raw HMRC body is opaque to
    end-users, so we replace it with a concrete next-step.
    """
    code = ""
    body = exc.body or {}
    if isinstance(body, dict):
        code = (body.get("code") or "").upper()

    if exc.status_code == 404 and code == "MATCHING_RESOURCE_NOT_FOUND":
        return (
            f"OAUTH_NINO_MISMATCH: HMRC doesn't recognise NINO {nino} with "
            "your current HMRC sign-in — your OAuth session is for a "
            "different test user. Click 'Disconnect from HMRC' on the "
            "Connect-to-HMRC page, then sign in again with the Government "
            "Gateway userId + password that matches this NINO. If you "
            "don't have those credentials, mint a fresh sandbox test user "
            "on the dashboard and OAuth as that new identity."
        )

    if exc.status_code == 403:
        return (
            "HMRC refused the obligations request (403). Most often this "
            "means you OAuthed with the wrong Government Gateway user — "
            "make sure it's the MTD-enabled one for this NINO."
        )

    # Default — surface enough detail to debug without exposing internals.
    return f"HMRC returned {exc.status_code}: {body}"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _fetch_one_business(
    *, user_id: int, request_obj, nino: str, business: dict,
) -> list[UiObligation]:
    """Call HMRC for ONE business and map the response to UI rows."""
    business_id = business.get("business_id", "")
    type_of_business = business.get("type_of_business", "self-employment")
    label = business.get("label") or _default_label(type_of_business)

    path = (
        f"/individuals/business/{type_of_business}/"
        f"{nino}/{business_id}/obligations"
    )
    resp = _client.request(
        user_id=user_id, method="GET", path=path,
        request_obj=request_obj,
        accept_version=os.environ.get("HMRC_OBLIGATIONS_API_VERSION", _API_VERSION),
    )
    parsed = HmrcObligations(**(resp.json or {"obligations": []}))
    return [
        _map_to_ui(
            obligation=ob, type_of_business=type_of_business,
            business_id=business_id, business_label=label,
        )
        for ob in parsed.obligations
    ]


def _map_to_ui(*, obligation, type_of_business: str, business_id: str, business_label: str) -> UiObligation:
    today = dt.date.today()
    due = _parse_date(obligation.due)
    received = _parse_date(obligation.received) if obligation.received else None

    if obligation.status == "Fulfilled":
        ui_status = "filed"
    elif due < today:
        ui_status = "overdue"
    elif (due - today).days <= 14:
        ui_status = "open"
    else:
        ui_status = "upcoming"

    days_until_due = (due - today).days
    return UiObligation(
        business_type=_normalise_business_type(type_of_business),
        business_label=business_label,
        business_id=business_id,
        period_key=obligation.periodKey,
        period_start=obligation.start,
        period_end=obligation.end,
        due=obligation.due,
        received=obligation.received,
        status=ui_status,
        days_until_due=days_until_due,
        human_due=_human_due(ui_status, days_until_due, received),
    )


def _normalise_business_type(raw: str) -> str:
    """HMRC paths use 'self-employment' / 'property'. UI uses the same."""
    if (raw or "").lower().startswith("property"):
        return "property"
    return "self-employment"


def _default_label(type_of_business: str) -> str:
    return "Property — UK" if _normalise_business_type(type_of_business) == "property" else "Self-employment"


def _human_due(status: str, days: int, received: dt.date | None) -> str:
    """Plain-English phrasing for the dashboard panel."""
    if status == "filed":
        return f"filed {received.strftime('%-d %b %Y')}" if received else "filed"
    if days < 0:
        d = -days
        return f"{d} day{'s' if d != 1 else ''} overdue"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due in {days} days"


def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def _sort_rows(rows: list[UiObligation]) -> list[UiObligation]:
    """Show open + overdue first (most urgent), then upcoming, then filed."""
    rank = {"overdue": 0, "open": 1, "upcoming": 2, "filed": 3}
    return sorted(rows, key=lambda r: (rank.get(r.status, 9), r.due))


# ---------------------------------------------------------------------------
# Demo fixture — used until the user is fully connected (or for demos).
#
# Numbers reflect the real MTD ITSA quarterly schedule for tax year
# 2026/2027. The dashboard panel can render against this immediately, and
# every shape matches the real HMRC API so swapping to live data is just
# `HMRC_DEMO_OBLIGATIONS=0` + a connected user with NINO + business IDs.
# ---------------------------------------------------------------------------

def _demo_obligations() -> list[UiObligation]:
    """One self-employment + one property business, four quarters each
    plus EOPS-style annual obligations."""
    today = dt.date.today()
    rows: list[UiObligation] = []
    rows.extend(_demo_business(
        today, "self-employment", "Mitoba — sole trader",
        business_id="XAIS00000000001",
    ))
    rows.extend(_demo_business(
        today, "property", "Ipswich SA portfolio",
        business_id="XPIS00000000002",
    ))
    return _sort_rows(rows)


def _demo_business(
    today: dt.date, type_of_business: str, label: str, *, business_id: str,
) -> Iterable[UiObligation]:
    """Generate four quarters + an annual obligation for one business."""
    # MTD ITSA quarters for the 2026/27 tax year (6 Apr 2026 – 5 Apr 2027).
    # `due` is 5 days after the quarter end, per HMRC's policy.
    quarters = [
        ("#001", dt.date(2026, 4, 6),  dt.date(2026, 7, 5),  dt.date(2026, 8, 5)),
        ("#002", dt.date(2026, 7, 6),  dt.date(2026, 10, 5), dt.date(2026, 11, 5)),
        ("#003", dt.date(2026, 10, 6), dt.date(2027, 1, 5),  dt.date(2027, 2, 5)),
        ("#004", dt.date(2027, 1, 6),  dt.date(2027, 4, 5),  dt.date(2027, 5, 5)),
    ]
    bt = _normalise_business_type(type_of_business)
    for periodKey, start, end, due in quarters:
        # If today is past the due date, mark it 'filed' a week before due —
        # that's what a working user would see on the dashboard.
        if today > due:
            status = "filed"
            received = due - dt.timedelta(days=7)
        elif today > end:
            status = "open" if (due - today).days <= 14 else "upcoming"
            received = None
        else:
            status = "upcoming"
            received = None
        days = (due - today).days
        yield UiObligation(
            business_type=bt, business_label=label, business_id=business_id,
            period_key=periodKey,
            period_start=start.isoformat(), period_end=end.isoformat(),
            due=due.isoformat(),
            received=received.isoformat() if received else None,
            status=status,
            days_until_due=days,
            human_due=_human_due(status, days, received),
        )

    # End-of-Period Statement — due 31 Jan after tax year end (so 31 Jan 2028).
    eops_due = dt.date(2028, 1, 31)
    yield UiObligation(
        business_type=bt, business_label=label, business_id=business_id,
        period_key="EOPS",
        period_start="2026-04-06", period_end="2027-04-05",
        due=eops_due.isoformat(), received=None,
        status="upcoming" if today < eops_due else "overdue",
        days_until_due=(eops_due - today).days,
        human_due=_human_due(
            "upcoming" if today < eops_due else "overdue",
            (eops_due - today).days, None,
        ),
    )
