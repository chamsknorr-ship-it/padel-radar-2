"""
Aggregation -> dashboard_data.json (Tag-für-Tag-Zeitreihe)

Statt fester Zeiträume liefert die Auswertung pro Club eine Tagesreihe. Das
Dashboard rechnet daraus jeden beliebigen Zeitraum selbst zusammen
(Heute/Woche/Monat sind nur Spezialfälle) und bildet den Gesamt-Bericht als
Summe über alle Clubs.

Pro Club:
  capacity:    Kapazitätsminuten PRO TAG je Court-Typ (Courts x Betriebsfenster)
  court_types: Anzahl Courts je Typ
  daily:       je beobachtetem Tag:
                 rev_m / rev_e         gemessener / geschätzter Umsatz
                 measured_min          gemessene gebuchte Minuten (für Wochentag)
                 types{label:{b,r}}    gebuchte Minuten + Umsatz je Typ (gemessen+geschätzt)
                 tod{bucket:min}        gemessene Minuten je Tageszeit
                 dur{bucket:count}      gemessene Buchungen je Länge
  events:      erkannte Event-Tage
"""

from __future__ import annotations

import json
from datetime import date, datetime

import store
from courts import court_type_label
from infer import DEFAULT_OP_START, DEFAULT_OP_END, duration_bucket

EVENT_THRESHOLD = 0.95  # ab dieser Tagesauslastung gilt ein Tag als "Event-Verdacht"

TOD_BUCKETS = ["Vormittag", "Mittag", "Nachmittag", "Abend", "Nachts"]

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

VORLAUF_BUCKETS = ["<2h", "<6h", "Gleicher Tag", "1 Tag", "2 Tage",
                   "3–7 Tage", "8–14 Tage", "15–30 Tage"]


def advance_bucket(h: float) -> str:
    if h < 2:   return "<2h"
    if h < 6:   return "<6h"
    if h < 24:  return "Gleicher Tag"
    if h < 48:  return "1 Tag"
    if h < 72:  return "2 Tage"
    if h < 168: return "3–7 Tage"
    if h < 336: return "8–14 Tage"
    return "15–30 Tage"


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


def _window(court: dict) -> int:
    s = court["op_start"] if court["op_start"] is not None else DEFAULT_OP_START
    e = court["op_end"] if court["op_end"] is not None else DEFAULT_OP_END
    return max(0, e - s)


def _detect_events(vcourts, vbooks, obs, courts, today) -> list:
    cap_per_day = sum(_window(c) for c in vcourts) or 1
    booked_per_day: dict[str, float] = {}
    for b in vbooks:
        if b["date"] in obs and b["kind"] in ("measured", "backlog"):
            booked_per_day[b["date"]] = booked_per_day.get(b["date"], 0) + b["duration_min"]
    events = []
    for d, bm in sorted(booked_per_day.items()):
        if d < today.isoformat():
            continue
        if bm / cap_per_day >= EVENT_THRESHOLD:
            events.append({"date": d, "name": "Ganztägig belegt", "note": "Event/Turnier-Verdacht"})
    return events[:8]


def build_dashboard(conn, today: date | None = None, ccy: str = "EUR") -> dict:
    today = today or date.today()
    courts = store.get_courts(conn)
    venues = store.get_venues(conn)

    courts_by_tenant: dict[str, list] = {}
    for c in courts.values():
        courts_by_tenant.setdefault(c["tenant_id"], []).append(c)

    observed: dict[str, set] = {}
    q = ("SELECT c.tenant_id AS t, s.date AS d FROM seen s "
         "JOIN courts c ON s.resource_id=c.resource_id")
    for r in conn.execute(q):
        observed.setdefault(r["t"], set()).add(r["d"])

    all_dates: set = set()
    for s in observed.values():
        all_dates |= s

    rows = store.get_bookings(conn, min(all_dates), max(all_dates)) if all_dates else []
    book_by_tenant: dict[str, list] = {}
    for b in rows:
        book_by_tenant.setdefault(b["tenant_id"], []).append(b)

    out_venues = []
    for v in venues:
        tid = v["tenant_id"]
        vcourts = courts_by_tenant.get(tid, [])
        if not vcourts:
            continue
        obs = observed.get(tid, set())

        cap_by_type: dict[str, float] = {}
        type_courts: dict[str, int] = {}
        for c in vcourts:
            lab = court_type_label({"size": c["size"], "location": c["location"]})
            cap_by_type[lab] = cap_by_type.get(lab, 0) + _window(c)
            type_courts[lab] = type_courts.get(lab, 0) + 1

        daily = {d: {"rev_m": 0.0, "rev_e": 0.0, "measured_min": 0.0,
                     "types": {}, "tod": {}, "dur": {},
                     "vorlauf": {}, "wd_adv": {}} for d in obs}

        for b in book_by_tenant.get(tid, []):
            d = b["date"]
            if d not in daily or b["kind"] not in ("measured", "backlog"):
                continue
            c = courts.get(b["resource_id"])
            lab = court_type_label({"size": c["size"], "location": c["location"]}) if c else "Unbekannt"
            rec = daily[d]
            t = rec["types"].setdefault(lab, {"b": 0.0, "r": 0.0})
            t["b"] += b["duration_min"]
            t["r"] += b["price_value"]
            if b["kind"] == "measured":
                rec["rev_m"] += b["price_value"]
                rec["measured_min"] += b["duration_min"]
                tb = tod_bucket(b["start_min"])
                rec["tod"][tb] = rec["tod"].get(tb, 0) + b["duration_min"]
                du = duration_bucket(b["duration_min"])
                rec["dur"][du] = rec["dur"].get(du, 0) + 1
                # Vorlaufzeit aus observed_at berechnen
                try:
                    slot_dt = datetime(
                        *[int(x) for x in b["date"].split("-")],
                        b["start_min"] // 60, b["start_min"] % 60)
                    obs_dt = datetime.fromisoformat(b["observed_at"])
                    if obs_dt.tzinfo:
                        obs_dt = obs_dt.replace(tzinfo=None)
                    adv_h = (slot_dt - obs_dt).total_seconds() / 3600
                    if adv_h >= 0:
                        bk = advance_bucket(adv_h)
                        rec["vorlauf"][bk] = rec["vorlauf"].get(bk, 0) + 1
                        wl = WEEKDAYS[date.fromisoformat(b["date"]).weekday()]
                        wa = rec["wd_adv"].setdefault(wl, {"s": 0.0, "n": 0})
                        wa["s"] = round(wa["s"] + adv_h / 24, 2)
                        wa["n"] += 1
                except Exception:
                    pass
            else:
                rec["rev_e"] += b["price_value"]

        # runden
        for rec in daily.values():
            rec["rev_m"] = round(rec["rev_m"], 1)
            rec["rev_e"] = round(rec["rev_e"], 1)
            rec["measured_min"] = round(rec["measured_min"], 1)
            for o in rec["types"].values():
                o["b"] = round(o["b"], 1)
                o["r"] = round(o["r"], 1)

        events = _detect_events(vcourts, book_by_tenant.get(tid, []), obs, courts, today)
        total_rev = sum(rec["rev_m"] + rec["rev_e"] for rec in daily.values())

        out_venues.append({
            "tenant_id": tid,
            "name": v["name"],
            "district": v.get("district") or v.get("address") or "",
            "lat": v.get("lat"),
            "lng": v.get("lng"),
            "courts": len(vcourts),
            "capacity": {k: round(val, 1) for k, val in cap_by_type.items()},
            "court_types": [{"label": k, "courts": type_courts[k]} for k in sorted(cap_by_type)],
            "daily": daily,
            "events": events,
            "_rev": total_rev,
        })

    out_venues.sort(key=lambda x: x.pop("_rev"), reverse=True)
    all_obs = sorted(all_dates)
    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "city": "Berlin",
        "date_min": all_obs[0] if all_obs else today.isoformat(),
        "date_max": all_obs[-1] if all_obs else today.isoformat(),
        "venues": out_venues,
    }


def write_dashboard(conn, path: str, today: date | None = None):
    data = build_dashboard(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
