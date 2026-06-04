"""
collector.py — laeuft alle 15 Minuten via GitHub Actions.

Diese Version nutzt eine FEST EINGEBAUTE Liste der Berliner Clubs (inkl. ihrer
Playtomic-IDs), weil Playtomic die automatische Umkreissuche von Server-IPs aus
blockiert. Die Verfuegbarkeits-Abfrage je Club bleibt der zuverlaessige Weg.

Ablauf:
  1. Clubs aus der eingebauten Liste laden (+ optional PADEL_TENANT_IDS)
  2. Verfuegbarkeit der naechsten 14 Tage je Club holen -> aktueller Snapshot
  3. Court-Stammdaten, Betriebsfenster und Durchschnittspreise aktualisieren
  4. Beim ERSTEN Sehen eines Tages: Altbestand schaetzen (kind=backlog)
  5. Aktuellen Snapshot mit dem letzten vergleichen -> neue Buchungen (measured)
  6. Snapshot speichern; 7. dashboard_data.json neu berechnen
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import store
from aggregate import write_dashboard
from courts import classify_court
from infer import (
    estimate_backlog, free_minutes, infer_bookings,
    normalize_snapshot, observed_window, price_per_minute,
)
from playtomic import PlaytomicClient

ROOT = Path(__file__).parent
DB_PATH = str(ROOT / "data" / "padel.db")
SNAPSHOT_PATH = ROOT / "data" / "last_snapshot.json"
DASHBOARD_PATH = str(ROOT / "dashboard_data.json")

DAYS = int(os.environ.get("PADEL_DAYS", "30"))

# Fest eingebaute Berliner Clubs: (tenant_id, Name, Adresse/Bezirk)
BERLIN_CLUBS = [
    ("d3edd4f5-1930-4d19-aba8-44d62f8f9da8", "Füchse Berlin", "Freiheitsweg 18"),
    ("16825678-053a-400d-b626-4c386d58706b", "Kickerworld Berlin Spandau", "Kl. Eiswerderstr. 1, Spandau"),
    ("31bb4900-5ad3-424a-a7fe-ed49ed2bf8e8", "Padel Arena Berlin", "Haberstraße 18"),
    ("6adc7add-0547-4f48-830e-153f9aa5948d", "Padel Berlin Ostkreuz", "Wiesenweg 1-4"),
    ("0220b0b5-c27a-4433-9c91-1798aaec5250", "Padelhaus", "Köpenicker Chaussee 11-14"),
    ("041a4a3c-8895-465d-91d1-c22f75049770", "TIO TIO Rooftop", "Marktstraße 6"),
    ("4a3497a5-f9bd-43eb-9aaa-a972a856b3d2", "PadelBros", "Wittestraße 46"),
    ("8b818dae-aacb-4ea3-aa7b-0e77b1149c85", "mitte — charlotte (Charlottenburg)", "Sophie-Charlotten-Straße 14"),
    ("632ca5b0-93bc-4718-a3e9-288bc2fe507d", "Padel Neukölln", "Oderstraße 182, Neukölln"),
    ("528af083-c941-480e-9d80-b79c82e5bd3f", "Padel Mitte", "Müllerstraße 185"),
    ("aa7e1831-a90d-4a4f-b6ae-f6d334179907", "BeachMitte", "Caroline-Michaelis-Str. 8"),
    ("ab298454-60d0-499d-8491-692db482421a", "Padel Factory", "Am Gewerbepark 5"),
    ("9fea856e-7d1a-4cae-9831-79015318967b", "PBC Center", "Großbeerenstraße 2-10"),
    ("d4506944-7eb2-40bd-b9ea-9ad4a769db04", "Birgit (Kreuzberg)", "Schleusenufer 3, Kreuzberg"),
    ("f6f12032-198e-4657-ab45-8aeb0b8a24b5", "Padel Lankwitz", "Leonorenstraße 37"),
    ("dbd1589f-a99f-41cb-93bb-33a95c0789ba", "Rainbow Padel", "Niederbarnimallee 116"),
]

BERLIN_DISTRICTS = [
    "Mitte", "Kreuzberg", "Friedrichshain", "Pankow", "Prenzlauer Berg",
    "Charlottenburg", "Wilmersdorf", "Spandau", "Steglitz", "Zehlendorf",
    "Tempelhof", "Schöneberg", "Neukölln", "Treptow", "Köpenick",
    "Marzahn", "Hellersdorf", "Lichtenberg", "Reinickendorf", "Wedding", "Lankwitz",
]

# Court-Typen je Club (von den Playtomic-Clubseiten ausgelesen).
# Liste je Club = (size, location) pro Platz. Die Reihenfolge dient nur der
# Zuordnung; entscheidend ist die ANZAHL je Typ.
def _c(size, location, n):
    return [(size, location)] * n

COURT_PROFILES = {
    "d3edd4f5-1930-4d19-aba8-44d62f8f9da8": _c("double", "outdoor", 2),                         # Füchse
    "16825678-053a-400d-b626-4c386d58706b": _c("double", "outdoor", 2),                         # Kickerworld
    "31bb4900-5ad3-424a-a7fe-ed49ed2bf8e8": _c("double", "outdoor", 10),                        # Padel Arena
    "6adc7add-0547-4f48-830e-153f9aa5948d": _c("double", "outdoor", 2) + _c("double", "indoor", 1),  # Ostkreuz
    "0220b0b5-c27a-4433-9c91-1798aaec5250": _c("double", "indoor", 5) + _c("single", "indoor", 2) + _c("double", "outdoor", 2),  # Padelhaus
    "041a4a3c-8895-465d-91d1-c22f75049770": _c("double", "outdoor", 5),                         # TIO TIO
    "4a3497a5-f9bd-43eb-9aaa-a972a856b3d2": _c("double", "indoor", 6) + _c("single", "indoor", 2),   # PadelBros
    "8b818dae-aacb-4ea3-aa7b-0e77b1149c85": _c("double", "indoor", 5),                          # mitte-charlotte
    "632ca5b0-93bc-4718-a3e9-288bc2fe507d": _c("double", "outdoor", 4),                         # Padel Neukölln
    "528af083-c941-480e-9d80-b79c82e5bd3f": _c("double", "outdoor", 4),                         # Padel Mitte
    "aa7e1831-a90d-4a4f-b6ae-f6d334179907": _c("double", "outdoor", 4),                         # BeachMitte
    "ab298454-60d0-499d-8491-692db482421a": _c("double", "indoor", 10) + _c("single", "indoor", 2),  # Padel Factory
    "9fea856e-7d1a-4cae-9831-79015318967b": _c("double", "indoor", 4) + _c("single", "indoor", 2),   # PBC Center
    "d4506944-7eb2-40bd-b9ea-9ad4a769db04": _c("double", "outdoor", 2) + _c("single", "outdoor", 1), # Birgit
    "f6f12032-198e-4657-ab45-8aeb0b8a24b5": _c("double", "outdoor", 4),                         # Padel Lankwitz
    "dbd1589f-a99f-41cb-93bb-33a95c0789ba": _c("double", "outdoor", 2),                         # Rainbow
}

# Koordinaten der Clubs (für die Karte) — von den Playtomic-Clubseiten.
COORDS = {
    "d3edd4f5-1930-4d19-aba8-44d62f8f9da8": (52.5760962, 13.3584063),
    "16825678-053a-400d-b626-4c386d58706b": (52.5488991, 13.2268255),
    "31bb4900-5ad3-424a-a7fe-ed49ed2bf8e8": (52.4625641, 13.4607792),
    "6adc7add-0547-4f48-830e-153f9aa5948d": (52.5074565, 13.4742189),
    "0220b0b5-c27a-4433-9c91-1798aaec5250": (52.4852487, 13.4973184),
    "041a4a3c-8895-465d-91d1-c22f75049770": (52.504191, 13.4735707),
    "4a3497a5-f9bd-43eb-9aaa-a972a856b3d2": (52.5762282, 13.3049313),
    "8b818dae-aacb-4ea3-aa7b-0e77b1149c85": (52.5200191, 13.2859645),
    "632ca5b0-93bc-4718-a3e9-288bc2fe507d": (52.4679985, 13.4192319),
    "528af083-c941-480e-9d80-b79c82e5bd3f": (52.5372399, 13.3694468),
    "aa7e1831-a90d-4a4f-b6ae-f6d334179907": (52.5334937, 13.3843963),
    "ab298454-60d0-499d-8491-692db482421a": (52.5212484, 13.5670361),
    "9fea856e-7d1a-4cae-9831-79015318967b": (52.439074, 13.3786568),
    "d4506944-7eb2-40bd-b9ea-9ad4a769db04": (52.4976551, 13.4508098),
    "f6f12032-198e-4657-ab45-8aeb0b8a24b5": (52.4428487, 13.345597),
    "dbd1589f-a99f-41cb-93bb-33a95c0789ba": (52.7285251, 13.4900338),
}


def district_from_address(address: str) -> str:
    a = (address or "").lower()
    for d in BERLIN_DISTRICTS:
        if d.lower() in a:
            return d
    return address or ""


def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Start")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(DASHBOARD_PATH).parent.mkdir(parents=True, exist_ok=True)

    conn = store.connect(DB_PATH)
    client = PlaytomicClient()
    now = datetime.now().astimezone()
    now_iso = now.isoformat(timespec="seconds")

    # --- 1. Clubs aus eingebauter Liste (+ optional Umgebungsvariable) ---
    clubs = list(BERLIN_CLUBS)
    extra = [x.strip() for x in os.environ.get("PADEL_TENANT_IDS", "").split(",") if x.strip()]
    known_ids = {c[0] for c in clubs}
    for tid in extra:
        if tid not in known_ids:
            clubs.append((tid, tid, ""))

    for tid, name, addr in clubs:
        lat, lng = COORDS.get(tid, (None, None))
        store.upsert_venue(conn, {
            "tenant_id": tid, "name": name, "address": addr,
            "district": district_from_address(addr), "lat": lat, "lng": lng,
        }, now_iso)
    print(f"  {len(clubs)} Clubs geladen.")

    # --- 2. Verfuegbarkeit holen -----------------------------------------
    raw_by_tenant: dict[str, list] = {}
    ok = 0
    for tid, name, _addr in clubs:
        try:
            data = client.fetch_availability_range(tid, DAYS)
            raw_by_tenant[tid] = data
            if data:
                ok += 1
        except RuntimeError as e:
            print(f"  ! {name}: {e}")
            raw_by_tenant[tid] = []
    print(f"  Verfügbarkeit erhalten von {ok}/{len(clubs)} Clubs.")

    curr = normalize_snapshot(raw_by_tenant, now)

    # Court-Typen je resource_id aus den Profilen ableiten ----------------
    res_by_tenant: dict[str, set] = {}
    for key in curr["courts"]:
        t, r, _ = key.split("|", 2)
        res_by_tenant.setdefault(t, set()).add(r)
    res_type: dict[str, tuple] = {}
    for t, rids in res_by_tenant.items():
        profile = COURT_PROFILES.get(t, [])
        for i, rid in enumerate(sorted(rids)):
            res_type[rid] = profile[i] if i < len(profile) else ("double", "unknown")

    # --- 3./4. Stammdaten, Fenster, Preise, Altbestand -------------------
    courts_seen = store.get_courts(conn)
    for key, slots in curr["courts"].items():
        tenant_id, resource_id, day = key.split("|", 2)
        size, location = res_type.get(resource_id, ("double", "unknown"))
        store.upsert_court(conn, tenant_id, resource_id, "", size, location)
        if resource_id not in courts_seen:
            courts_seen[resource_id] = {"op_start": None, "op_end": None}
        if not slots:
            continue
        op_s, op_e = observed_window(slots)
        store.update_court_window(conn, resource_id, op_s, op_e)
        store.add_court_price(conn, resource_id, price_per_minute(slots, op_s, op_e))

        if store.is_first_sight(conn, resource_id, day, now_iso):
            row = store.get_courts(conn).get(resource_id, {})
            bl = estimate_backlog(day, slots,
                                  row.get("op_start") or op_s,
                                  row.get("op_end") or op_e, now)
            if bl:
                store.add_booking(conn, {
                    "tenant_id": tenant_id, "resource_id": resource_id, "date": day,
                    "start_min": 0, "duration_min": bl["duration_min"],
                    "price_value": bl["price_value"], "ccy": bl["ccy"], "kind": "backlog",
                }, now_iso)

    # --- 5. Vergleich mit letztem Snapshot -> gemessene Buchungen ---------
    if SNAPSHOT_PATH.exists():
        prev = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        bookings = infer_bookings(prev, curr)
        for b in bookings:
            store.add_booking(conn, b, now_iso)
        print(f"  {len(bookings)} neue Buchungen erkannt.")
    else:
        print("  Erster Lauf – kein Vergleich, nur Altbestand-Schätzung.")

    # --- 6. Snapshot speichern -------------------------------------------
    SNAPSHOT_PATH.write_text(json.dumps(curr, ensure_ascii=False), encoding="utf-8")

    # --- 7. Dashboard berechnen ------------------------------------------
    conn.commit()
    data = write_dashboard(conn, DASHBOARD_PATH)
    conn.close()
    print(f"  Dashboard aktualisiert: {len(data['venues'])} Clubs.")
    print("Fertig.")


if __name__ == "__main__":
    main()
