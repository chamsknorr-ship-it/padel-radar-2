"""
Court-Klassifizierung.

Playtomic liefert pro Court eine `resource_id` und (in den Stammdaten) einen
Namen + ggf. Eigenschaften. Daraus leiten wir zwei Dimensionen ab:

  size     -> "single" | "double" | "unknown"
  location -> "indoor" | "outdoor" | "unknown"

Die Erkennung läuft über Schlüsselwörter im Court-Namen und in den
Playtomic-Eigenschaften (Deutsch / Englisch / Spanisch). Falls nichts passt,
bleibt der Wert "unknown" und wird im Dashboard separat ausgewiesen.
"""

from __future__ import annotations

# Schlüsselwörter (alles kleingeschrieben verglichen)
INDOOR_WORDS = ["indoor", "halle", "hall", "cubierta", "cubierto", "interior", "covered", "drinnen"]
OUTDOOR_WORDS = ["outdoor", "draussen", "draußen", "freiluft", "exterior", "descubierta", "open air", "aussen", "außen", "freiplatz"]
SINGLE_WORDS = ["single", "individual", "einzel", "1v1", "solo"]
DOUBLE_WORDS = ["double", "doble", "doppel", "2v2", "standard"]


def _match(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def classify_court(name: str | None, properties: dict | None = None) -> dict:
    """
    Gibt {"size": ..., "location": ...} zurück.

    name:       Court-Name, z.B. "Padel Indoor 3 (Single)"
    properties: optionales dict aus den Playtomic-Stammdaten
    """
    name = (name or "").lower()
    props_text = ""
    if properties:
        # Eigenschaften flach in einen Text gießen, damit Stichwörter greifen
        props_text = " ".join(str(v).lower() for v in _flatten(properties))

    haystack = f"{name} {props_text}"

    # --- Location: indoor / outdoor ---
    location = "unknown"
    if _match(haystack, INDOOR_WORDS):
        location = "indoor"
    elif _match(haystack, OUTDOOR_WORDS):
        location = "outdoor"

    # --- Size: single / double (Standard = double) ---
    # Ein Standard-Padelplatz ist ein Doppelplatz; Single-Plätze werden fast
    # immer ausdrücklich benannt. Ohne Treffer nehmen wir daher "double" an.
    size = "double"
    if _match(haystack, SINGLE_WORDS):
        size = "single"
    elif _match(haystack, DOUBLE_WORDS):
        size = "double"

    return {"size": size, "location": location}


def court_type_label(klass: dict) -> str:
    """Menschlich lesbares Label, z.B. 'Double · Indoor'."""
    size_map = {"single": "Single", "double": "Double", "unknown": "Größe ?"}
    loc_map = {"indoor": "Indoor", "outdoor": "Outdoor", "unknown": "Lage ?"}
    return f"{size_map[klass['size']]} · {loc_map[klass['location']]}"


def _flatten(obj):
    """Hilfsfunktion: verschachtelte dicts/lists in flache Werte zerlegen."""
    out = []
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_flatten(v))
    else:
        out.append(obj)
    return out
