"""
Aggregation -> dashboard_data.json

Berechnet je Club und je Zeitraum (Heute / Woche / Monat):
  - Auslastung (gebuchte Minuten / Kapazitätsminuten der beobachteten Tage)
  - Umsatz, getrennt nach gemessen (kind=measured) und geschätzt (kind=backlog)
  - Aufschlüsselung nach Court-Typ und nach Buchungslänge
  - erkannte Events (Tage, an denen praktisch alles ganztägig belegt war)

Wichtig: Es werden nur Tage in den Nenner aufgenommen, die wir tatsächlich
beobachtet haben. Tage weiter als das Vorlauf-Fenster in der Zukunft zählen
also nicht als "leer" mit – sonst würde die Auslastung künstlich sinken.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import store
from courts import court_type_label
from infer import DEFAULT_OP_START, DEFAULT_OP_END, duration_bucket

EVENT_THRESHOLD = 0.95  # ab dieser Tagesauslastung gilt ein Tag als "Event-Verdacht"

# Tageszeit-Fenster (nach Startzeit der Buchung)
TOD_BUCKETS = ["Vormittag", "Mittag", "Nachmittag", "Abend", "Nachts"]


def tod_bucket(start_min: int) -> str:
    h = start_min / 60
    if 6 <= h < 11:
        return "Vormittag"
    if 11 <= h < 14:
        return "Mittag"
    if 14 <= h < 17:
        return "Nachmittag"
    if 17 <= h < 22:
        return "Abend"
    return "Nachts"


def period_dates(period: str, today: date) -> list[str]:
    if period == "today":
        days = [today]
    elif period == "week":
        monday = today - timedelta(days=today.weekday())
        days = [monday + timedelta(days=i) for i in range(7)]
    elif period == "month":
        first = today.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        days = [first + timedelta(days=i) for i in range((nxt - first).days)]
    else:
        days = [today]
    return [d.isoformat() for d in days]


def _window(court: dict) -> int:
    s = court["op_start"] if court["op_start"] is not None else DEFAULT_OP_START
    e = court["op_end"] if court["op_end"] is not None else DEFAULT_OP_END
    return max(0, e - s)


def build_dashboard(conn, today: date | None = None, ccy: str = "EUR") -> dict:
    today = today or date.today()
    courts = store.get_courts(conn)
    venues = store.get_venues(conn)

    # Courts je Club
    courts_by_tenant: dict[str, list] = {}
    for c in courts.values():
        courts_by_tenant.setdefault(c["tenant_id"], []).append(c)

    # Beobachtete Tage je Club (aus seen-Tabelle)
    observed: dict[str, set] = {}
    q = ("SELECT c.tenant_id AS t, s.date AS d FROM seen s "
         "JOIN courts c ON s.resource_id=c.resource_id")
    for r in conn.execute(q):
        observed.setdefault(r["t"], set()).add(r["d"])

    # Alle relevanten Buchungen laden (großzügiges Fenster)
    all_days = period_dates("month", today) + period_dates("week", today) + [today.isoformat()]
    lo, hi = min(all_days), max(all_days)
    rows = store.get_bookings(conn, lo, hi)
    book_by_tenant: dict[str, list] = {}
    for b in rows:
        book_by_tenant.setdefault(b["tenant_id"], []).append(b)

    periods = ["today", "week", "month"]
    out_venues = []

    for v in venues:
        tid = v["tenant_id"]
        vcourts = courts_by_tenant.get(tid, [])
        if not vcourts:
            continue
        vbooks = book_by_tenant.get(tid, [])
        obs = observed.get(tid, set())

        metrics, by_type, by_duration, by_timeofday = {}, {}, {}, {}
        for pk in periods:
            pdays = [d for d in period_dates(pk, today) if d in obs]
            pdays_set = set(pdays)
            n_days = len(pdays)

            # Kapazität & Buchungen je Court-Typ
            type_cap: dict[str, float] = {}
            type_book: dict[str, float] = {}
            type_rev: dict[str, float] = {}
            type_courts: dict[str, int] = {}
            for c in vcourts:
                tlabel = court_type_label({"size": c["size"], "location": c["location"]})
                type_cap[tlabel] = type_cap.get(tlabel, 0) + _window(c) * n_days
                type_courts[tlabel] = type_courts.get(tlabel, 0) + 1

            meas_rev = est_rev = booked_min = 0.0
            dur_counts: dict[str, int] = {}
            tod_min: dict[str, float] = {}
            for b in vbooks:
                if b["date"] not in pdays_set:
                    continue
                if b["kind"] not in ("measured", "backlog"):
                    continue
                c = courts.get(b["resource_id"])
                tlabel = court_type_label({"size": c["size"], "location": c["location"]}) if c else "Unbekannt"
                type_book[tlabel] = type_book.get(tlabel, 0) + b["duration_min"]
                type_rev[tlabel] = type_rev.get(tlabel, 0) + b["price_value"]
                booked_min += b["duration_min"]
                if b["kind"] == "measured":
                    meas_rev += b["price_value"]
                    bucket = duration_bucket(b["duration_min"])
                    dur_counts[bucket] = dur_counts.get(bucket, 0) + 1
                    tb = tod_bucket(b["start_min"])
                    tod_min[tb] = tod_min.get(tb, 0) + b["duration_min"]
                else:
                    est_rev += b["price_value"]

            total_cap = sum(type_cap.values()) or 1
            metrics[pk] = {
                "occupancy": round(booked_min / total_cap, 4),
                "rev_measured": round(meas_rev, 0),
                "rev_estimated": round(est_rev, 0),
                "booked_hours": round(booked_min / 60, 1),
                "observed_days": n_days,
                "ccy": ccy,
            }
            by_type[pk] = [
                {
                    "label": t,
                    "courts": type_courts.get(t, 0),
                    "occupancy": round(type_book.get(t, 0) / (type_cap[t] or 1), 4),
                    "revenue": round(type_rev.get(t, 0), 0),
                }
                for t in sorted(type_cap.keys())
            ]
            tot = sum(dur_counts.values()) or 1
            by_duration[pk] = {k: round(dur_counts.get(k, 0) / tot, 3) for k in ["1h", "1,5h", "2h"]}
            tt = sum(tod_min.values()) or 1
            by_timeofday[pk] = {k: round(tod_min.get(k, 0) / tt, 3) for k in TOD_BUCKETS}

        # Event-Erkennung (innerhalb beobachteter Tage)
        events = _detect_events(vcourts, vbooks, obs, courts, today)

        out_venues.append({
            "tenant_id": tid,
            "name": v["name"],
            "district": v.get("district") or v.get("address") or "",
            "lat": v.get("lat"),
            "lng": v.get("lng"),
            "courts": len(vcourts),
            "metrics": metrics,
            "by_type": by_type,
            "by_duration": by_duration,
            "by_timeofday": by_timeofday,
            "events": events,
        })

    # nach Monats-Umsatz sortieren
    out_venues.sort(
        key=lambda v: v["metrics"]["month"]["rev_measured"] + v["metrics"]["month"]["rev_estimated"],
        reverse=True,
    )

    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "city": "Berlin",
        "periods": periods,
        "venues": out_venues,
    }


def _detect_events(vcourts, vbooks, obs, courts, today) -> list:
    """Tage markieren, an denen praktisch alle Courts ganztägig belegt waren."""
    cap_per_day = sum(_window(c) for c in vcourts) or 1
    booked_per_day: dict[str, float] = {}
    for b in vbooks:
        if b["date"] in obs and b["kind"] in ("measured", "backlog"):
            booked_per_day[b["date"]] = booked_per_day.get(b["date"], 0) + b["duration_min"]
    events = []
    for d, bm in sorted(booked_per_day.items()):
        if d < today.isoformat():
            continue  # nur heute/zukünftig melden
        if bm / cap_per_day >= EVENT_THRESHOLD:
            events.append({"date": d, "name": "Ganztägig belegt", "note": "Event/Turnier-Verdacht"})
    return events[:6]


def write_dashboard(conn, path: str, today: date | None = None):
    data = build_dashboard(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
