"""Schnelltests für die Ableitungs-Logik. Aufruf: python test_infer.py"""
from datetime import datetime

from infer import (
    parse_price, free_minutes, contiguous_blocks,
    normalize_snapshot, infer_bookings,
)


def test_parse_price():
    assert parse_price("72 EUR") == (72.0, "EUR")
    assert parse_price("42,50 EUR") == (42.5, "EUR")
    assert parse_price("€30") == (30.0, "EUR")
    assert parse_price(None) == (0.0, "EUR")
    print("ok  parse_price")


def test_free_minutes_and_blocks():
    slots = [[1080, 60, 30, "EUR"], [1080, 90, 42, "EUR"]]  # 18:00/60 + 18:00/90
    fm = free_minutes(slots)
    assert min(fm) == 1080 and max(fm) == 1080 + 90 - 1  # Vereinigung = 18:00-19:30
    assert contiguous_blocks(fm) == [(1080, 1170)]
    print("ok  free_minutes/contiguous_blocks")


def test_overlapping_not_double_counted():
    """6 überlappende Optionen, aber nur EINE 60-Min-Buchung soll rauskommen."""
    tid = "club1"
    day = "2025-12-20"  # weit in der Zukunft -> kein Aging-Effekt
    prev_raw = {tid: [{"resource_id": "A", "start_date": day, "slots": [
        {"start_time": "18:00:00", "duration": 60, "price": "30 EUR"},
        {"start_time": "18:00:00", "duration": 90, "price": "42 EUR"},
        {"start_time": "18:00:00", "duration": 120, "price": "52 EUR"},
        {"start_time": "18:30:00", "duration": 60, "price": "30 EUR"},
        {"start_time": "18:30:00", "duration": 90, "price": "42 EUR"},
        {"start_time": "19:00:00", "duration": 60, "price": "30 EUR"},
    ]}]}
    # Kunde bucht 18:00-19:00; übrig bleibt 19:00-20:00 (buchbar)
    curr_raw = {tid: [{"resource_id": "A", "start_date": day, "slots": [
        {"start_time": "19:00:00", "duration": 60, "price": "30 EUR"},
    ]}]}
    t = datetime(2025, 12, 1, 10, 0, 0)
    prev = normalize_snapshot(prev_raw, t)
    curr = normalize_snapshot(curr_raw, t)
    b = infer_bookings(prev, curr)
    assert len(b) == 1, f"erwartet 1 Buchung, bekam {len(b)}"
    assert b[0]["duration_min"] == 60, b[0]
    assert b[0]["start_min"] == 1080, b[0]  # 18:00
    assert 25 <= b[0]["price_value"] <= 40, b[0]
    print(f"ok  überlappende Slots -> 1 Buchung, 60 Min, {b[0]['price_value']} EUR")


def test_aging_out_ignored():
    """Slot, der heute kurz vor Start verschwindet, ist KEINE Buchung."""
    tid = "club1"
    today = datetime.now().date().isoformat()
    # Snapshot um 14:00; ein Slot um 14:30 (zu nah) + ein Slot um 20:00 (echt)
    prev_raw = {tid: [{"resource_id": "B", "start_date": today, "slots": [
        {"start_time": "14:30:00", "duration": 60, "price": "30 EUR"},
        {"start_time": "20:00:00", "duration": 60, "price": "30 EUR"},
    ]}]}
    curr_raw = {tid: [{"resource_id": "B", "start_date": today, "slots": []}]}
    t = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
    prev = normalize_snapshot(prev_raw, t)
    curr = normalize_snapshot(curr_raw, t)
    b = infer_bookings(prev, curr)
    starts = [x["start_min"] for x in b]
    assert 14 * 60 + 30 not in starts, "14:30 hätte ignoriert werden müssen"
    assert 20 * 60 in starts, "20:00 hätte als Buchung zählen müssen"
    print(f"ok  Aging-out korrekt ignoriert (erkannte Starts: {starts})")


if __name__ == "__main__":
    test_parse_price()
    test_free_minutes_and_blocks()
    test_overlapping_not_double_counted()
    test_aging_out_ignored()
    print("\nAlle Tests bestanden.")
