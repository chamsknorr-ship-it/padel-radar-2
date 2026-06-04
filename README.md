# Padel Radar Berlin

Ein selbstlaufendes Dashboard, das Auslastung und geschätzten Umsatz aller
Berliner Padel-Clubs aus der öffentlich sichtbaren Playtomic-Verfügbarkeit
auswertet. Läuft kostenlos auf GitHub – ohne eigenen Server, ohne Programmieren.

In dieser Version liegen ALLE Dateien flach nebeneinander (kein Unterordner),
damit das Hochladen einfach ist.

## So funktioniert es
Alle 15 Minuten wird die Verfügbarkeit jedes Clubs abgefragt. Verschwindet ein
freier Slot zwischen zwei Abfragen (und liegt noch weit genug in der Zukunft),
gilt er als gebucht. Daraus werden Auslastung und Umsatz berechnet – getrennt
nach "live gemessen" und "geschätztem Altbestand".

## Einrichtung (alles im Browser, kein Terminal)

1. GitHub-Konto anlegen auf github.com.
2. Neues Repository: + (oben rechts) -> New repository, Name z.B. padel-radar,
   auf PUBLIC stellen, Create.
3. Dateien hochladen: Link "uploading an existing file" (oder Add file ->
   Upload files). ZIP entpacken, dann ALLE Dateien aus dem Ordner ins Fenster
   ziehen (alles lose Dateien - keine Ordner zu beachten). Commit changes.
4. Workflow-Datei anlegen (startet die Automatik): Add file -> Create new file.
   Als Pfad oben exakt eintippen: .github/workflows/collect.yml
   Inhalt aus der Datei collect.yml im Paket hineinkopieren. Commit changes.
5. Automatik einschalten: Reiter Actions -> ggf. gruenen "enable"-Knopf klicken.
6. Dashboard-Webseite einschalten: Settings -> Pages -> Source "Deploy from a
   branch", Branch main, Ordner "/ (root)", Save. Adresse erscheint oben:
   https://DEIN-NAME.github.io/padel-radar/
7. Erstes Sammeln starten: Actions -> Collect -> Run workflow. 1-3 Min warten.
8. Oeffnen - fertig. Aktualisiert sich ab jetzt alle 15 Minuten von selbst.

## Auf dem iPhone als App
Adresse in Safari oeffnen -> Teilen -> Zum Home-Bildschirm.

## Was du am Anfang siehst
Erst Beispieldaten (gelber Hinweis), die beim ersten echten Lauf ersetzt werden.
Am ersten Tag ist viel "geschaetzt" und wenig "gemessen" - das wird mit der Zeit
genauer und vollstaendiger.

## Einstellungen aendern
In .github/workflows/collect.yml unter env:
- PADEL_RADIUS_M  - Suchradius in Metern (Standard 30000)
- PADEL_DAYS      - Tage in die Zukunft (Standard 14)
- PADEL_LAT/LNG   - Mittelpunkt der Suche
- PADEL_TENANT_IDS - feste Club-IDs, falls die Umkreissuche nichts findet

## Hinweise
- Alle Zahlen sind SCHAETZUNGEN aus oeffentlich sichtbarer Verfuegbarkeit.
- Es werden KEINE personenbezogenen Daten gesammelt.
- Automatisiertes Auslesen kann Playtomics Nutzungsbedingungen widersprechen;
  die Frequenz ist bewusst moderat. Rechtliche Einschaetzung liegt bei dir.
