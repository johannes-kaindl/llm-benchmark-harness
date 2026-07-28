# Die Web-Steuerzentrale nutzen

`touchstone gui` startet einen lokalen Web-Server, über den du den gesamten Ablauf bedienen
kannst: sehen, was ein Pack prüft, Läufe konfigurieren und starten, live zusehen, Ergebnisse
lesen, über Maschinen vergleichen und exportieren.

Wichtig zum Verständnis: die GUI **misst nichts**. Sie ist eine
out-of-process Control-Plane und startet exakt dieselben `touchstone eval` / `touchstone judge`
Subprozesse, die du sonst selbst tippen würdest. `runs/` bleibt die einzige Quelle der
Wahrheit; alles, was die GUI erzeugt, ist über die CLI genauso lesbar — und umgekehrt
([ADR 0014](../decisions/0014-gui-control-plane.md)).

## Starten

Die GUI ist ein **optionales Extra**, damit der Mess-Kern keine Web-Abhängigkeiten schleppt:

```bash
uv sync --extra gui
uv run touchstone gui
```

Oder das mitgelieferte Startskript, das das Extra bei Bedarf selbst nachinstalliert und alle
Argumente durchreicht:

```bash
./start-gui.sh                 # Auto-Port, öffnet den Browser
./start-gui.sh --port 8000     # fester Port
./start-gui.sh --no-open       # ohne Browser (Remote, curl)
./start-gui.sh --runs ./runs   # anderes runs-Verzeichnis
```

Der Server bindet auf **127.0.0.1** — er ist im Netz nicht erreichbar. Beenden mit `Ctrl-C`.
Fehlt das Extra, sagt `touchstone gui` das mit dem passenden Installationsbefehl und bricht ab.

## Der typische Weg durch die Oberfläche

1. **Übersicht** (`/`) — alle Bundles unter `runs/`, mit Status. Von hier startest du alles.
2. **Pack ansehen** — was prüft dieses Pack, nach welchen Dimensionen, mit welchem K.-o.?
   Die Prompts stehen mitsamt Green- und Red-Flags da; das ist der schnellste Weg,
   ein fremdes Pack zu verstehen, ohne YAML zu lesen.
3. **Konfigurieren und starten** — Config und Pack wählen, aus den vom Endpoint tatsächlich
   angebotenen Modellen auswählen, starten. Es läuft **ein Mess-Lauf gleichzeitig** —
   parallele Läufe würden sich gegenseitig die Speicher-Messung verderben.
4. **Live zusehen** — Fortschritt pro Zelle, während der Subprozess läuft.
5. **Bewerten** — `judge` über ein fertiges Bundle, Judge-Modell aus der Liste des
   Judge-Endpoints wählbar.
6. **Ergebnis lesen** — Scorecard, Tech-Specs, Antworten und Judge-Begründungen pro Prompt.
7. **Vergleichen** — mehrere Bundles nebeneinander; separat auch der Judge-Vergleich
   (dasselbe Bundle, von verschiedenen Judges bewertet).
8. **Exportieren** — einzelne Dateien, ganze Bundles, Report-Markdown oder CSV.

Gelöschte Bundles landen zuerst im **Papierkorb** und lassen sich zurückholen, bis du sie
endgültig entfernst.

## Der Pack-Editor

Packs lassen sich in der Oberfläche bearbeiten und **validieren, bevor** sie gespeichert
werden — dieselbe Prüfung, die auch der CLI-Loader fährt (Skala 1–5, eindeutige IDs, gültige
K.-o.-Dimension, `curated`-Abdeckung). Für den strukturellen Aufbau eines Packs siehe
[Einen eigenen Pack bauen](eigenen-pack-bauen.md).

## Wenn etwas nicht geht

- **Judge-Modelle erscheinen nicht in der Auswahl** — der Judge-Endpoint läuft nicht.
  `start-gui.sh` warnt beim Start, wenn `:1234` nicht antwortet. Übersicht und Ergebnisse
  ansehen geht auch ohne; bewerten nicht.
- **„GUI-Abhängigkeiten fehlen"** — `uv sync --extra gui` nachholen.
- **Ein Lauf hängt** — die GUI hat einen Stop-Knopf; er beendet den Subprozess. Weil das
  Bundle inkrementell auf die Platte geschrieben wird, ist nichts verloren: setz denselben Lauf
  danach per `--resume` fort, in der GUI oder auf der CLI.
- **Ein Bundle taucht nicht auf** — die GUI liest `runs/`. Liegt es woanders, starte mit
  `--runs <pfad>` oder importiere es über die Bundle-Import-Funktion.

## Verwandt

- [ADR 0014 — GUI als out-of-process Control-Plane](../decisions/0014-gui-control-plane.md)
- [Tutorial](../tutorial.md) — derselbe Ablauf auf der CLI
- [Einen eigenen Pack bauen](eigenen-pack-bauen.md)
