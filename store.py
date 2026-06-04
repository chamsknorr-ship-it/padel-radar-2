"""
Speicher (SQLite). Hält dauerhaft:
  - venues  : die Clubs
  - courts  : die Plätze (mit erkanntem Typ + Betriebsfenster + Ø Preis/Min)
  - bookings: die abgeleiteten Buchungen (kind: measured | backlog | event)
  - seen    : welche (Court, Tag) wir schon einmal gesehen haben (für Altbestand)

Die Datei (data/padel.db) wird vom GitHub-Workflow nach jedem Lauf
zurück ins Repo committet, damit die Historie erhalten bleibt.
"""

from __future__ import annotations

import sqlite3
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
  tenant_id TEXT PRIMARY KEY,
  name TEXT, district TEXT, address TEXT,
  lat REAL, lng REAL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS courts (
  resource_id TEXT PRIMARY KEY,
  tenant_id TEXT, name TEXT,
  size TEXT, location TEXT,
  op_start INTEGER, op_end INTEGER,
  ppm_sum REAL DEFAULT 0, ppm_n INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT, resource_id TEXT, date TEXT,
  start_min INTEGER, duration_min INTEGER,
  price_value REAL, ccy TEXT, kind TEXT, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS seen (
  resource_id TEXT, date TEXT, first_seen_at TEXT,
  PRIMARY KEY (resource_id, date)
);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(date);
CREATE INDEX IF NOT EXISTS idx_bookings_tenant ON bookings(tenant_id);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_venue(conn, v: dict, updated_at: str):
    conn.execute(
        """INSERT INTO venues (tenant_id,name,district,address,lat,lng,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(tenant_id) DO UPDATE SET
             name=excluded.name, district=excluded.district,
             address=excluded.address, lat=excluded.lat, lng=excluded.lng,
             updated_at=excluded.updated_at""",
        (v["tenant_id"], v["name"], v.get("district", ""), v.get("address", ""),
         v.get("lat"), v.get("lng"), updated_at),
    )


def upsert_court(conn, tenant_id, resource_id, name, size, location):
    conn.execute(
        """INSERT INTO courts (resource_id,tenant_id,name,size,location)
           VALUES (?,?,?,?,?)
           ON CONFLICT(resource_id) DO UPDATE SET
             name=excluded.name, size=excluded.size, location=excluded.location""",
        (resource_id, tenant_id, name, size, location),
    )


def update_court_window(conn, resource_id, op_start, op_end):
    """Betriebsfenster aufweiten (frühester Start / spätestes Ende je gesehen)."""
    row = conn.execute("SELECT op_start, op_end FROM courts WHERE resource_id=?",
                       (resource_id,)).fetchone()
    if row is None:
        return
    cur_s, cur_e = row["op_start"], row["op_end"]
    new_s = op_start if cur_s is None else min(cur_s, op_start)
    new_e = op_end if cur_e is None else max(cur_e, op_end)
    conn.execute("UPDATE courts SET op_start=?, op_end=? WHERE resource_id=?",
                 (new_s, new_e, resource_id))


def add_court_price(conn, resource_id, ppm: float):
    if ppm <= 0:
        return
    conn.execute(
        "UPDATE courts SET ppm_sum=ppm_sum+?, ppm_n=ppm_n+1 WHERE resource_id=?",
        (ppm, resource_id),
    )


def court_avg_ppm(conn, resource_id) -> float:
    row = conn.execute("SELECT ppm_sum, ppm_n FROM courts WHERE resource_id=?",
                       (resource_id,)).fetchone()
    if row and row["ppm_n"]:
        return row["ppm_sum"] / row["ppm_n"]
    return 0.0


def is_first_sight(conn, resource_id, day: str, first_seen_at: str) -> bool:
    """True, wenn (Court, Tag) zum ersten Mal gesehen wird (und markiert ihn)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO seen (resource_id,date,first_seen_at) VALUES (?,?,?)",
        (resource_id, day, first_seen_at),
    )
    return cur.rowcount == 1


def add_booking(conn, b: dict, observed_at: str):
    conn.execute(
        """INSERT INTO bookings
           (tenant_id,resource_id,date,start_min,duration_min,price_value,ccy,kind,observed_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (b["tenant_id"], b["resource_id"], b["date"], b["start_min"],
         b["duration_min"], b["price_value"], b["ccy"], b["kind"], observed_at),
    )


def get_courts(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT * FROM courts"):
        out[r["resource_id"]] = dict(r)
    return out


def get_venues(conn) -> list:
    return [dict(r) for r in conn.execute("SELECT * FROM venues")]


def get_bookings(conn, start_date: str, end_date: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM bookings WHERE date>=? AND date<=?", (start_date, end_date))]
