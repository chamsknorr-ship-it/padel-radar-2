"""
Ableitungs-Logik ("inference").

Grundidee:
  - Für jeden Court + Tag bilden wir die Menge der FREIEN Minuten
    (Vereinigung aller angebotenen Slot-Intervalle). So zählen überlappende
    Buchungs-Optionen (z.B. 18:00/60 und 18:00/90) NICHT doppelt.
  - Vergleich zweier Snapshots: Minuten, die vorher frei waren und jetzt
    nicht mehr -> wahrscheinlich gebucht.
  - Schutz gegen Fehlalarme: Slots, deren Start zu nah an "jetzt" liegt,
    verschwinden von selbst (man kann nicht 5 Min vorher buchen). Solche
    zählen wir nicht als Buchung.
  - Preis je Buchung: Preis-pro-Minute aus den überlappenden Slots des
    vorherigen Snapshots -> mal Buchungslänge. Robust bei jeder Länge.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# Minuten in der Zukunft, ab denen ein verschwundener Slot als echte Buchung
# gilt (darunter: vermutlich nur "zu spät zum Buchen").
MIN_FUTURE_MINUTES = 90

# Standard-Betriebsfenster, falls für einen Court noch nichts bekannt ist.
DEFAULT_OP_START = 9 * 60   # 09:00
DEFAULT_OP_END = 23 * 60    # 23:00

DURATION_BUCKETS = [60, 90, 120]  # 1h / 1,5h / 2h


# ---------------------------------------------------------------------- #
# Preis parsen: "72 EUR" / "72.50 EUR" / "€72" -> (72.0, "EUR")
# ---------------------------------------------------------------------- #
def parse_price(price_str: str | None) -> tuple[float, str]:
    if not price_str:
        return (0.0, "EUR")
    s = str(price_str).strip()
    num = re.search(r"[\d]+(?:[.,]\d+)?", s)
    value = float(num.group(0).replace(",", ".")) if num else 0.0
    ccy_match = re.search(r"[A-Z]{3}", s.upper())
    ccy = ccy_match.group(0) if ccy_match else ("EUR" if "€" in s else "EUR")
    return (value, ccy)


def hhmmss_to_min(t: str) -> int:
    """'18:30:00' -> 1110 (Minuten ab Mitternacht)."""
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])


# ---------------------------------------------------------------------- #
# Snapshot-Normalisierung
# ---------------------------------------------------------------------- #
def normalize_snapshot(raw_by_tenant: dict[str, list], taken_at: datetime) -> dict:
    """
    raw_by_tenant: {tenant_id: [ {resource_id, start_date, slots:[...]}, ... ]}
    Ergebnis:
      {
        "taken_at": iso,
        "courts": { "tenant|resource|YYYY-MM-DD": [ [start_min,dur,price,ccy], ... ] }
      }
    """
    courts: dict[str, list] = {}
    for tenant_id, entries in raw_by_tenant.items():
        for entry in entries:
            rid = entry.get("resource_id")
            day = entry.get("start_date")
            if not rid or not day:
                continue
            key = f"{tenant_id}|{rid}|{day}"
            slots = []
            for s in entry.get("slots", []):
                try:
                    start = hhmmss_to_min(s["start_time"])
                    dur = int(s["duration"])
                    val, ccy = parse_price(s.get("price"))
                    slots.append([start, dur, val, ccy])
                except (KeyError, ValueError, TypeError):
                    continue
            courts.setdefault(key, []).extend(slots)
    return {"taken_at": taken_at.isoformat(), "courts": courts}


# ---------------------------------------------------------------------- #
# Intervall-Mathematik (Minuten-Mengen)
# ---------------------------------------------------------------------- #
def free_minutes(slots: list[list]) -> set[int]:
    """Vereinigung aller [start, start+dur) Intervalle -> Menge freier Minuten."""
    out: set[int] = set()
    for start, dur, *_ in slots:
        out.update(range(start, start + dur))
    return out


def contiguous_blocks(minutes: set[int]) -> list[tuple[int, int]]:
    """Sortierte Minuten -> Liste zusammenhängender Blöcke [(start, end_exklusiv), ...]."""
    if not minutes:
        return []
    ms = sorted(minutes)
    blocks = []
    start = prev = ms[0]
    for m in ms[1:]:
        if m == prev + 1:
            prev = m
        else:
            blocks.append((start, prev + 1))
            start = prev = m
    blocks.append((start, prev + 1))
    return blocks


def price_per_minute(slots: list[list], block_start: int, block_end: int) -> float:
    """Mittlerer Preis/Minute der Slots, die den Block überlappen."""
    rates = []
    for start, dur, val, _ccy in slots:
        if dur <= 0 or val <= 0:
            continue
        if start < block_end and (start + dur) > block_start:  # Überlappung
            rates.append(val / dur)
    if not rates:
        # Fallback: bester Preis/Minute aus allen Slots
        rates = [val / dur for start, dur, val, _ in slots if dur > 0 and val > 0]
    return sum(rates) / len(rates) if rates else 0.0


def duration_bucket(minutes: int) -> str:
    """Buchungslänge dem nächsten Standard-Bucket zuordnen (Label)."""
    best = min(DURATION_BUCKETS, key=lambda b: abs(b - minutes))
    return {60: "1h", 90: "1,5h", 120: "2h"}[best]


# ---------------------------------------------------------------------- #
# Der Vergleich: zwei Snapshots -> neue Buchungen
# ---------------------------------------------------------------------- #
def infer_bookings(prev: dict, curr: dict) -> list[dict]:
    """
    Vergleicht prev und curr und gibt Liste abgeleiteter Buchungen zurück:
      {tenant_id, resource_id, date, start_min, duration_min, price_value, ccy,
       kind: "measured"}
    """
    now = datetime.fromisoformat(curr["taken_at"])
    today_str = now.date().isoformat()
    now_min = now.hour * 60 + now.minute

    bookings = []
    prev_courts = prev.get("courts", {})
    curr_courts = curr.get("courts", {})

    for key, prev_slots in prev_courts.items():
        curr_slots = curr_courts.get(key, [])
        free_prev = free_minutes(prev_slots)
        free_curr = free_minutes(curr_slots)
        newly_occupied = free_prev - free_curr
        if not newly_occupied:
            continue

        tenant_id, resource_id, day = key.split("|", 2)

        for b_start, b_end in contiguous_blocks(newly_occupied):
            # Schutz: zu nah an "jetzt" -> wahrscheinlich nur abgelaufen
            if day == today_str and b_start < now_min + MIN_FUTURE_MINUTES:
                continue
            dur = b_end - b_start
            if dur < 25:  # Mini-Reste ignorieren
                continue
            ppm = price_per_minute(prev_slots, b_start, b_end)
            bookings.append({
                "tenant_id": tenant_id,
                "resource_id": resource_id,
                "date": day,
                "start_min": b_start,
                "duration_min": dur,
                "price_value": round(ppm * dur, 2),
                "ccy": prev_slots[0][3] if prev_slots else "EUR",
                "kind": "measured",
            })
    return bookings


# ---------------------------------------------------------------------- #
# Betriebsfenster + Altbestand-Schätzung (Buchungen vor Monitoring-Start)
# ---------------------------------------------------------------------- #
def observed_window(slots: list[list]) -> tuple[int, int]:
    """Frühester Slot-Start und spätestes Slot-Ende in diesen Slots."""
    if not slots:
        return (DEFAULT_OP_START, DEFAULT_OP_END)
    start = min(s[0] for s in slots)
    end = max(s[0] + s[1] for s in slots)
    return (start, end)


def estimate_backlog(
    day: str,
    slots: list[list],
    op_start: int,
    op_end: int,
    taken_at: datetime,
) -> dict | None:
    """
    Einmalige Schätzung beim ERSTEN Sichten eines (Court, Tag): wie viel war
    schon belegt, bevor wir hinschauen konnten?

    backlog_minuten = Betriebsfenster − aktuell freie Minuten
    Preis = Preis/Minute (aus aktuellen Slots) × backlog_minuten
    """
    # Bei heute: bereits vergangene Stunden nicht als "gebucht" werten
    if day == taken_at.date().isoformat():
        now_min = taken_at.hour * 60 + taken_at.minute
        op_start = max(op_start, now_min)
    if op_end <= op_start:
        return None

    operating = op_end - op_start
    free_now = {m for m in free_minutes(slots) if op_start <= m < op_end}
    occupied = operating - len(free_now)
    if occupied <= 0:
        return None

    ppm = price_per_minute(slots, op_start, op_end)
    return {
        "duration_min": occupied,
        "price_value": round(ppm * occupied, 2),
        "ccy": slots[0][3] if slots else "EUR",
        "operating_min": operating,
    }
