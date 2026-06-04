"""
collector.py — das Skript, das der GitHub-Workflow alle 15 Minuten startet.

Ablauf:
  1. Berliner Clubs finden (oder bekannte aus der DB nutzen)
  2. Verfügbarkeit der nächsten 14 Tage je Club holen  -> aktueller Snapshot
  3. Court-Stammdaten, Betriebsfenster und Ø-Preise aktualisieren
  4. Beim ERSTEN Sehen eines Tages: Altbestand schätzen (kind=backlog)
  5. Aktuellen Snapshot mit dem letzten vergleichen -> neue Buchungen (kind=measured)
  6. Aktuellen Snapshot speichern (für den nächsten Lauf)
  7. dashboard_data.json neu berechnen

Konfiguration über Umgebungsvariablen (mit sinnvollen Standardwerten):
  PADEL_LAT, PADEL_LNG, PADEL_RADIUS_M, PADEL_DAYS
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

LAT = float(os.environ.get("PADEL_LAT", "52.5200"))       # Berlin Mitte
LNG = float(os.environ.get("PADEL_LNG", "13.4050"))
RADIUS_M = int(os.environ.get("PADEL_RADIUS_M", "30000"))  # 30 km deckt ganz Berlin
DAYS = int(os.environ.get("PADEL_DAYS", "14"))

# Berliner Bezirke für die "district"-Anzeige
BERLIN_DISTRICTS = [
    "Mitte", "Kreuzberg", "Friedrichshain", "Pankow", "Prenzlauer Berg",
    "Charlottenburg", "Wilmersdorf", "Spandau", "Steglitz", "Zehlendorf",
    "Tempelhof", "Schöneberg", "Neukölln", "Treptow", "Köpenick",
    "Marzahn", "Hellersdorf", "Lichtenberg", "Reinickendorf", "Wedding",
]


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

    # --- 1. Clubs finden -------------------------------------------------
    try:
        tenants = client.discover_tenants(LAT, LNG, RADIUS_M)
        print(f"  {len(tenants)} Clubs über Umkreissuche gefunden.")
    except RuntimeError as e:
        print(f"  ! Clubsuche fehlgeschlagen: {e}")
        tenants = []

    # Fallback / Ergänzung: manuell gesetzte Tenant-IDs (Komma-getrennt)
    manual = [x.strip() for x in os.environ.get("PADEL_TENANT_IDS", "").split(",") if x.strip()]
    have = {t["tenant_id"] for t in tenants}
    for tid in manual:
        if tid not in have:
            det = client.fetch_tenant_detail(tid)
            if det:
                tenants.append(det)
            else:
                tenants.append({"tenant_id": tid, "name": tid, "address": "", "courts": []})

    for t in tenants:
        # Falls keine Courts mitgeliefert wurden: Stammdaten nachladen
        if not t.get("courts"):
            det = client.fetch_tenant_detail(t["tenant_id"])
            if det:
                t["name"] = det.get("name") or t.get("name")
                t["address"] = det.get("address") or t.get("address", "")
                t["courts"] = det.get("courts", [])
        t["district"] = district_from_address(t.get("address", ""))
        store.upsert_venue(conn, t, now_iso)
        for c in t.get("courts", []):
            klass = classify_court(c.get("name"), c.get("properties"))
            store.upsert_court(conn, t["tenant_id"], c["resource_id"],
                               c.get("name", ""), klass["size"], klass["location"])

    known = store.get_venues(conn)
    if not known:
        print("  Keine Clubs bekannt – Abbruch.")
        write_dashboard(conn, DASHBOARD_PATH)
        conn.commit()
        return

    # --- 2. Verfügbarkeit holen -----------------------------------------
    raw_by_tenant: dict[str, list] = {}
    for v in known:
        tid = v["tenant_id"]
        try:
            raw_by_tenant[tid] = client.fetch_availability_range(tid, DAYS)
        except RuntimeError as e:
            print(f"  ! {v['name']}: {e}")
            raw_by_tenant[tid] = []

    curr = normalize_snapshot(raw_by_tenant, now)

    # --- 3./4. Stammdaten, Fenster, Preise, Altbestand ------------------
    courts_seen = store.get_courts(conn)
    for key, slots in curr["courts"].items():
        tenant_id, resource_id, day = key.split("|", 2)
        if resource_id not in courts_seen:
            store.upsert_court(conn, tenant_id, resource_id, "", "unknown", "unknown")
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

    # --- 5. Vergleich mit letztem Snapshot -> gemessene Buchungen --------
    if SNAPSHOT_PATH.exists():
        prev = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        bookings = infer_bookings(prev, curr)
        for b in bookings:
            store.add_booking(conn, b, now_iso)
        print(f"  {len(bookings)} neue Buchungen erkannt.")
    else:
        print("  Erster Lauf – kein Vergleich, nur Altbestand-Schätzung.")

    # --- 6. Snapshot speichern ------------------------------------------
    SNAPSHOT_PATH.write_text(json.dumps(curr, ensure_ascii=False), encoding="utf-8")

    # --- 7. Dashboard berechnen -----------------------------------------
    conn.commit()
    data = write_dashboard(conn, DASHBOARD_PATH)
    conn.close()
    print(f"  Dashboard aktualisiert: {len(data['venues'])} Clubs.")
    print("Fertig.")


if __name__ == "__main__":
    main()
