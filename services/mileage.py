"""
HMRC mileage allowance — claims for business car/motorcycle/bicycle journeys.

UK rates (2025-26, refresh annually):
  Car / van:    45p per mile for the first 10,000 miles
                25p per mile thereafter
  Motorcycle:   24p per mile (flat)
  Bicycle:      20p per mile (flat)

The 10k threshold is per tax year (6 April to 5 April). We re-bucket
every time the user adds a journey — never stored, always recomputed
from the full ledger.
"""
from __future__ import annotations

import datetime as _dt

import database


_BAND_1_LIMIT_MILES = 10_000.0
_CAR_BAND_1_RATE = 0.45
_CAR_BAND_2_RATE = 0.25
_MOTORCYCLE_RATE = 0.24
_BICYCLE_RATE = 0.20


def _tax_year_start(d: _dt.date) -> _dt.date:
    if d.month < 4 or (d.month == 4 and d.day < 6):
        return _dt.date(d.year - 1, 4, 6)
    return _dt.date(d.year, 4, 6)


def _tax_year_label(start: _dt.date) -> str:
    return f"{start.year}-{str(start.year + 1)[-2:]}"


def _journey_rate(vehicle: str, miles_so_far_this_year: float, miles: float) -> tuple[float, float]:
    """Compute (rate_avg, claim_amount) for a journey of `miles` given the
    user has already done `miles_so_far_this_year` business miles. Car
    journeys can straddle the 10k boundary so we split."""
    if vehicle == "motorcycle":
        return (_MOTORCYCLE_RATE, miles * _MOTORCYCLE_RATE)
    if vehicle == "bicycle":
        return (_BICYCLE_RATE, miles * _BICYCLE_RATE)
    # Default = car
    band_1_remaining = max(0.0, _BAND_1_LIMIT_MILES - miles_so_far_this_year)
    band_1_miles = min(miles, band_1_remaining)
    band_2_miles = miles - band_1_miles
    claim = band_1_miles * _CAR_BAND_1_RATE + band_2_miles * _CAR_BAND_2_RATE
    rate_avg = (claim / miles) if miles > 0 else 0.0
    return (rate_avg, claim)


def add_mileage_log(
    user_id: int,
    *,
    date_iso: str,
    miles: float,
    from_location: str | None = None,
    to_location: str | None = None,
    purpose: str | None = None,
    vehicle: str = "car",
    business_pct: int = 100,
) -> int:
    if miles <= 0:
        raise ValueError("miles must be positive")
    if vehicle not in ("car", "motorcycle", "bicycle"):
        raise ValueError(f"invalid vehicle: {vehicle}")
    if not (0 <= business_pct <= 100):
        raise ValueError("business_pct must be 0-100")
    return database._execute_insert(
        """INSERT INTO mileage_logs
        (user_id, date_iso, from_location, to_location, miles, purpose,
         vehicle, business_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, date_iso, from_location, to_location, float(miles),
         purpose, vehicle, int(business_pct)),
    )


def list_mileage_logs(user_id: int, limit: int = 1000) -> list[dict]:
    return database._fetchall_dicts(
        "SELECT * FROM mileage_logs WHERE user_id = ? "
        "ORDER BY date_iso DESC, id DESC LIMIT ?",
        (user_id, int(limit)),
    )


def delete_mileage_log(user_id: int, log_id: int) -> bool:
    """Delete one log entry. Returns True if a row was removed."""
    row = database._fetchone_dict(
        "SELECT id FROM mileage_logs WHERE id = ? AND user_id = ?",
        (log_id, user_id),
    )
    if not row:
        return False
    database._execute(
        "DELETE FROM mileage_logs WHERE id = ? AND user_id = ?",
        (log_id, user_id),
    )
    return True


def mileage_summary(user_id: int) -> dict:
    """Per-tax-year + per-vehicle summary of mileage and HMRC claim.

    Returns:
      {
        "tax_year": "2026-27",
        "tax_year_start": "2026-04-06",
        "totals": {
          "car_miles": 8230.0,
          "motorcycle_miles": 0,
          "bicycle_miles": 0,
          "total_claim_gbp": 3703.50,
          "band_1_remaining": 1770.0,   # 10k - car_miles
        },
        "logs": [
          {..., "rate_avg": 0.45, "claim_gbp": 4.50, "tax_year": "2026-27"},
          ...
        ],
      }
    """
    today = _dt.date.today()
    current_ty_start = _tax_year_start(today)

    logs = list_mileage_logs(user_id, limit=10_000)
    # Process oldest-first so we can compute the running 10k threshold.
    logs_chrono = list(reversed(logs))

    # Track car-miles per tax year (only car gets the 10k threshold)
    car_miles_by_ty: dict[str, float] = {}

    enriched: list[dict] = []
    for log in logs_chrono:
        try:
            d = _dt.datetime.strptime(log["date_iso"], "%Y-%m-%d").date()
        except (ValueError, TypeError, KeyError):
            continue
        ty_start = _tax_year_start(d)
        ty_label = _tax_year_label(ty_start)
        bpct = (log.get("business_pct") or 100) / 100.0
        miles_business = float(log["miles"]) * bpct
        vehicle = log.get("vehicle") or "car"

        if vehicle == "car":
            so_far = car_miles_by_ty.get(ty_label, 0.0)
            rate_avg, claim = _journey_rate(vehicle, so_far, miles_business)
            car_miles_by_ty[ty_label] = so_far + miles_business
        else:
            rate_avg, claim = _journey_rate(vehicle, 0.0, miles_business)

        enriched.append({
            "id": log["id"],
            "date_iso": log["date_iso"],
            "from_location": log.get("from_location"),
            "to_location": log.get("to_location"),
            "miles": float(log["miles"]),
            "purpose": log.get("purpose"),
            "vehicle": vehicle,
            "business_pct": log.get("business_pct") or 100,
            "rate_avg": round(rate_avg, 3),
            "claim_gbp": round(claim, 2),
            "tax_year": ty_label,
        })

    # Sort newest first for display
    enriched.sort(key=lambda r: (r["date_iso"], r["id"]), reverse=True)

    # Totals = THIS tax year only
    current_ty = _tax_year_label(current_ty_start)
    this_year_logs = [r for r in enriched if r["tax_year"] == current_ty]
    car_miles = sum(
        r["miles"] * (r["business_pct"]/100.0)
        for r in this_year_logs if r["vehicle"] == "car"
    )
    moto_miles = sum(
        r["miles"] * (r["business_pct"]/100.0)
        for r in this_year_logs if r["vehicle"] == "motorcycle"
    )
    bike_miles = sum(
        r["miles"] * (r["business_pct"]/100.0)
        for r in this_year_logs if r["vehicle"] == "bicycle"
    )
    total_claim = sum(r["claim_gbp"] for r in this_year_logs)

    return {
        "tax_year": current_ty,
        "tax_year_start": current_ty_start.isoformat(),
        "totals": {
            "car_miles": round(car_miles, 1),
            "motorcycle_miles": round(moto_miles, 1),
            "bicycle_miles": round(bike_miles, 1),
            "total_claim_gbp": round(total_claim, 2),
            "band_1_remaining": max(0.0, round(_BAND_1_LIMIT_MILES - car_miles, 1)),
        },
        "logs": enriched,
    }
