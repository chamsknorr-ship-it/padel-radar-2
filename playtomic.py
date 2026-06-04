"""
Playtomic-Client.

Zwei öffentliche Endpunkte (kein Login nötig):

  1) Clubs in einer Region finden:
     GET https://playtomic.io/api/v1/tenants
         ?coordinate=LAT,LNG&radius=METER&sport_id=PADEL&...

  2) Verfügbarkeit eines Clubs für einen Tag:
     GET https://api.playtomic.io/v1/availability
         ?sport_id=PADEL&tenant_id=...&start_min=...&start_max=...
     (max. 25 Stunden pro Anfrage -> wir fragen pro Tag einzeln)

Wir verhalten uns "leise": echter Browser-User-Agent, kleine zufällige
Wartezeiten, Wiederholungen bei Fehlern.
"""

from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta

import requests

TENANTS_URL = "https://playtomic.io/api/v1/tenants"
AVAILABILITY_URL = "https://api.playtomic.io/v1/availability"

# Realistischer Header-Satz, damit die Anfragen wie ein normaler Client aussehen.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "X-Requested-With": "com.playtomic.app 6.13.0",
}


class PlaytomicClient:
    def __init__(self, min_delay: float = 0.4, max_delay: float = 1.1, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout

    def _sleep(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _get(self, url: str, params: dict, retries: int = 4):
        last_err = None
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                # 429 = zu viele Anfragen -> länger warten
                if r.status_code in (429, 503):
                    time.sleep(5 * (attempt + 1))
                    continue
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except requests.RequestException as e:
                last_err = str(e)
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Anfrage fehlgeschlagen ({url}): {last_err}")

    # ------------------------------------------------------------------ #
    # 1) Clubs finden
    # ------------------------------------------------------------------ #
    def discover_tenants(
        self,
        lat: float,
        lng: float,
        radius_m: int = 30000,
        page_size: int = 50,
        max_pages: int = 10,
    ) -> list[dict]:
        """
        Alle Padel-Clubs im Umkreis finden. Gibt eine Liste normalisierter
        Club-dicts zurück: {tenant_id, name, address, lat, lng, courts:[...]}.
        """
        found: dict[str, dict] = {}
        for page in range(max_pages):
            params = {
                "user_id": "me",
                "playtomic_status": "ACTIVE",
                "coordinate": f"{lat},{lng}",
                "sport_id": "PADEL",
                "radius": str(radius_m),
                "size": str(page_size),
                "page": str(page),
            }
            data = self._get(TENANTS_URL, params)
            items = data if isinstance(data, list) else data.get("tenants", data.get("results", []))
            if not items:
                break
            for t in items:
                tenant = self._normalize_tenant(t)
                if tenant and tenant["tenant_id"] not in found:
                    found[tenant["tenant_id"]] = tenant
            if len(items) < page_size:
                break
            self._sleep()
        return list(found.values())

    @staticmethod
    def _normalize_tenant(t: dict) -> dict | None:
        tid = t.get("tenant_id") or t.get("id")
        if not tid:
            return None
        addr = t.get("address", {}) or {}
        coord = addr.get("coordinate", {}) or t.get("coordinate", {}) or {}
        # Courts/Resourcen aus den Stammdaten ziehen (Name + Eigenschaften)
        courts = []
        for r in (t.get("resources", []) or []):
            if (r.get("sport_id") or r.get("sport")) not in (None, "PADEL"):
                continue
            courts.append({
                "resource_id": r.get("resource_id") or r.get("id"),
                "name": r.get("name", ""),
                "properties": r.get("properties", {}) or {},
            })
        return {
            "tenant_id": tid,
            "name": t.get("tenant_name") or t.get("name", "Unbekannt"),
            "address": ", ".join(
                str(x) for x in [addr.get("street"), addr.get("city")] if x
            ),
            "lat": coord.get("lat"),
            "lng": coord.get("lon") or coord.get("lng"),
            "courts": courts,
        }

    # ------------------------------------------------------------------ #
    # 2) Verfügbarkeit holen
    # ------------------------------------------------------------------ #
    def fetch_tenant_detail(self, tenant_id: str) -> dict | None:
        """Stammdaten eines einzelnen Clubs (Name + Courts) nachladen."""
        try:
            data = self._get(f"{TENANTS_URL}/{tenant_id}", {"user_id": "me"})
        except RuntimeError:
            return None
        if isinstance(data, dict):
            return self._normalize_tenant(data)
        return None

    def fetch_availability_day(self, tenant_id: str, day: date) -> list[dict]:
        """
        Verfügbarkeit eines Clubs für genau einen Tag.
        Rückgabe (roh von Playtomic):
          [{resource_id, start_date, slots:[{start_time, duration, price}]}]
        """
        start_min = datetime.combine(day, datetime.min.time())
        start_max = start_min + timedelta(hours=24, minutes=59)
        params = {
            "sport_id": "PADEL",
            "tenant_id": tenant_id,
            "start_min": start_min.strftime("%Y-%m-%dT%H:%M:%S"),
            "start_max": start_max.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        data = self._get(AVAILABILITY_URL, params)
        return data if isinstance(data, list) else []

    def fetch_availability_range(self, tenant_id: str, days: int) -> list[dict]:
        """Verfügbarkeit für die nächsten `days` Tage (heute eingeschlossen)."""
        out = []
        today = date.today()
        for i in range(days):
            day = today + timedelta(days=i)
            try:
                out.extend(self.fetch_availability_day(tenant_id, day))
            except RuntimeError as e:
                print(f"  ! {tenant_id} {day}: {e}")
            self._sleep()
        return out
