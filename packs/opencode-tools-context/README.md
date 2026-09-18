# Vorkontext für die Langkontext-Items (L1–L3) von `opencode-tools.yaml`

Eingefrorener Schnappschuss von zwölf Kernmodulen dieses Repos (Stand 2351223).
Die Items lesen diese Dateien als frühere `read`-Ergebnisse unter `/work/proj/src/touchstone/<name>.py`
ein, bevor die eigentliche Aufgabe kommt. So entstehen ~50k Token realistischer Code-Kontext wie in einer
laufenden opencode-Sitzung.

**Nicht aktualisieren**, wenn sich die Originale ändern. Ein anderer Kontext macht Läufe untereinander
unvergleichbar. Die Endung `.txt` hält die Kopien aus ruff, mypy und pytest heraus.
