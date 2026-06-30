"""
Einmaliges Aufräum-Skript.

Entfernt alle geschätzten Altbestand-Buchungen (kind='backlog') aus der
Datenbank und baut dashboard_data.json neu auf – danach enthält das
Dashboard nur noch tatsächlich gemessene Buchungen.

Nutzung (lokal oder als einmaliger GitHub-Actions-Schritt):
    python cleanup_backlog.py
"""

import sqlite3

import store
from aggregate import write_dashboard

DB_PATH = "data/padel.db"
DASHBOARD_PATH = "dashboard_data.json"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    before = conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"]
    backlog = conn.execute(
        "SELECT COUNT(*) AS n FROM bookings WHERE kind='backlog'").fetchone()["n"]

    conn.execute("DELETE FROM bookings WHERE kind='backlog'")
    conn.commit()

    after = conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"]
    print(f"Buchungen vorher:      {before}")
    print(f"davon backlog gelöscht: {backlog}")
    print(f"Buchungen nachher:     {after}  (nur noch gemessen)")

    # Dashboard mit den bereinigten Daten neu schreiben
    write_dashboard(conn, DASHBOARD_PATH)
    print(f"{DASHBOARD_PATH} neu geschrieben.")
    conn.close()


if __name__ == "__main__":
    main()
