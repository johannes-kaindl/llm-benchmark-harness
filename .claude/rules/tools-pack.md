---
paths:
  - "touchstone/toolbench.py"
  - "packs/opencode-tools.yaml"
  - "packs/opencode-tools-context/**"
  - "tests/test_toolbench.py"
  - "tests/test_toolbench_turns.py"
  - "tests/test_tools_exit_code.py"
---

- **Zwei Hashes, zwei Fragen: `pack_fingerprint` (Resume) und `checks_fingerprint` (Vergleich).** Der strenge `pack_fingerprint` (ganze YAML + Kontextdateien) bewacht `--resume`: Ein Parameterwechsel mitten in einem Bundle würde Zellen unter verschiedenen Bedingungen mischen. Der Vergleich fragt etwas anderes — „wurden dieselben **Checks** gemessen" — und benutzt `checks_fingerprint` (Items, Checks, Fixtures, Tool-Schemas, Rahmen-Prompts, Token- **und** Zugbudget; **ohne** `sampling`). Grund: Zwei Effort-Stufen, jede mit ihrem vom Hersteller empfohlenen Sampling (Thinking `1.0`/`0.95` vs. Instruct `0.7`/`0.80`), sind verschiedene Läufe **desselben** Prüfstands; der frühere Ein-Hash-Wächter verweigerte genau diesen Vergleich (gemessen 2026-09-20 gegen Hetzner). `tools-compare` **benennt** den Sampling-Unterschied jetzt als Warnzeile im Bericht, statt abzubrechen — ein Teil des Unterschieds ist dann Sampling und nicht Denkstufe, und das muss der Leser sehen. Alte Bundles ohne `checks_sha256` fallen auf den strengen Hash zurück. `bundle.json` schreibt beide.
- **`finish_reason` gilt über alle Züge, `max_turns` ist 5 — beides aus dem ersten v3-Lauf gelernt.** Der Mehrzug-Lauf endet mit einem Abschlusszug **ohne** Aufruf, also `finish_reason: stop`. Ein Item, das `tool_calls` verlangt (M1, L1), fiel deshalb durch, obwohl es die Aufgabe erfüllt hatte (gemessen 2026-09-20 gegen Hetzner). Der Check prüft jetzt **irgendeinen** Zug (`ToolTurn.finishes` trägt die Liste; `finish_reason` bleibt der des letzten Zugs, also das Ende der Episode). Und `max_turns` steht auf **5**, nicht 3: Eine echte opencode-Sitzung brauchte für eine vergleichbare Aufgabe 4–5 Schritte (von der opencode-Session gemessen: `build` 4, ihre alte Thinking-Konfiguration 5), mit 3 wurden die mehrstufigen Items **mitten in der Erkundung** abgeschnitten (M3 verbrauchte alle drei Züge mit `read`/`bash`). Preis: Jeder Zug schickt den ganzen Verlauf erneut, das Budget kostet also Prefill — bei den Langkontext-Items (L*) ist das der dominierende Posten.
- **Die Schleife reicht das Reasoning des vorigen Zugs zurück — unter BEIDEN Feldnamen, nicht nur `reasoning_content`.** Von der opencode-Session am Draht gemessen (2026-09-20, opencode 1.18.31): Die assistant-Nachricht des zweiten Zugs trägt `content`, `reasoning_content`, `role`, `tool_calls`; opencode liest **beide** Lieferformen (`delta.reasoning` wie Hetzner, `delta.reasoning_content` wie LM Studio) und schickt immer `reasoning_content` zurück. Die Gegenstelle liest es auch: gegen Hetzner 668 statt 343 Prompt-Token, und das Modell kannte ein Codewort, das **nur** im Reasoning stand. `follow_up_messages` bildete das zunächst nur mit `reasoning_content` nach — bis eine Cross-Session-Messung (2026-09-21) gegen einen anderen Remote-Endpoint zeigte, dass dessen Tool-Chain **ausschließlich** `reasoning` liest und mit nur `reasoning_content` jede Kette nach genau zwei Zügen still abbricht (`finish_reason stop`, leerer Content, HTTP 200 — sieht wie Modellversagen aus, ist aber unser Serialisierungs-Bug). Seit Commit `fade3f6` schickt `follow_up_messages` deshalb **beide** Feldnamen gleichzeitig in derselben assistant-Nachricht — kostet ein paar Bytes, kostet aber nie eine Kette. Ohne das Echo würde ein Thinking-Modell bei uns ohnehin ohne Erinnerung an seinen eigenen vorigen Schritt weiterarbeiten, also etwas anderes als der Agent, den wir messen wollen. (Nebenbefund von der opencode-Session: `opencode run --format json` gibt **kein** `reasoning`-Ereignis aus, auch lokal nicht — eine Lücke der Ausgabe, kein Verwerfen. Ungeprüft bleibt, ob Reasoning früherer Runden nach einer neuen Nutzer-Nachricht erhalten bleibt; Qwens Template verwirft es dort üblicherweise mit Absicht.)
- **`temperature`/`seed` werden bei `None` wirklich weggelassen, nicht als 0.0/42 gesendet (`ToolsSampling`, Commit `b97255b`, 2026-09-22).** Bis dahin schickte `stream_tools()` beide Felder immer mit, egal was der Pack-YAML trug — ein Client, der sie nie setzt (z. B. opencode ohne `temperature: true` am Modell-Eintrag, verwirft den Wert dann ersatzlos), ließ sich damit nicht replizieren; jede Messung „wie der echte Client es schickt" maß in Wahrheit die eigene 0.0/42-Vorgabe. Jetzt `float | None` / `int | None`: `None` lässt das Feld im Request-Body ganz weg. Cross-Session-Messung (2026-09-22, gpt-oss-Modell hinter einem Harmony-übersetzenden Proxy) bestätigte den Unterschied handfest — mit expliziten 0.0/42 blieb `completion_tokens` über 20 Wiederholungen exakt konstant, ohne beide Felder schwankte es spürbar; die Auslassung greift also wirklich. **Nebenbefund derselben Messreihe** (235 Direkt-Requests, 0 Kontextlecks à la „Klartext-JSON statt Tool-Call" — über vier Sampling-Varianten, 1–10 Tool-Schemas und Kontextgrößen bis ~19k Token, alles einzeln und kombiniert): Ein extern über einen Coding-Agent beobachteter, nicht-deterministischer Fehlermodus (~15–32 % der Läufe) ließ sich am nackten Endpunkt mit keiner dieser drei Variablen reproduzieren — Sampling, Werkzeugmenge und Kontextlänge sind damit als alleinige Ursache ausgeschlossen. Offene, unbelegte Hypothese: eine Harmony→OpenAI-SSE-Übersetzung (Proxy-Schicht) legt die Tool-Call-Argumente unter bestimmten, noch unbekannten Bedingungen in den Content-Kanal statt in `delta.tool_calls` — nicht weiter verfolgt, keine Repo-Aufgabe hier (der betroffene Endpoint ist projektfremd, Details dazu bewusst nicht in diesem AGPL-Repo).

- **Das Tools-Pack wertet die Summe aller Züge, nicht den ersten (Pack v3, 2026-09-20).** v2 wertete
  nur den ersten Zug und bestrafte damit Umsicht: Gegen Hetzner waren **alle 11 Durchfaller** dasselbe
  Muster — das Modell rief erst `ls` / `find` / `read` / `mkdir -p` und erst danach die Zielaktion, die
  in opencodes Schleife folgen würde (`none` tat das nie → 24/24, xhigh 20/24; der Unterschied war die
  Metrik, nicht das Modell). v3 fährt eine **Agenten-Schleife**: Auf jeden Aufruf bekommt das Modell ein
  gestelltes Tool-Ergebnis (`tool_result_for`: `read` auf eine Fixture → die echte Datei im
  opencode-Format, `read` auf Unbekanntes → „not found", sonst die im Item hinterlegte Antwort
  (`turn_results`, Muster → Ausgabe, Daten wie alles andere) oder die neutrale Quittung `(exit 0)` —
  **nie erfundene Dateiinhalte**) und darf weitermachen, bis es keine Aufrufe mehr macht oder
  `pack.max_turns` (3) erschöpft ist. Die Schleife endet **nicht**, wenn die Checks erfüllt sind — das
  wäre Wertung im Lauf. `merge_turns` führt die Züge zu dem einen Turn zusammen, gegen den die Checks
  laufen (Aufrufe aneinander, `prompt_tokens` vom letzten Zug, Dauern summiert, Fehler des letzten);
  `n_turns` + `turn_calls` stehen in `responses.jsonl`, CSV und Bericht, denn **jeder Zug kostet in
  opencode einen weiteren Request**. Mit der Schleife wurde `arg_equals`/`arg_regex` von „der **erste**
  Aufruf des Tools" auf „**irgendein** Aufruf" umgestellt; ein *verbotenes* Muster (`absent: true`)
  gilt spiegelbildlich für **jeden** Aufruf, sonst entschuldigte ein harmloses `ls` das verbotene Flag
  daneben. Exakte `calls`-Zahlen auf `bash` sind zu `min` geworden (Erkundung darf einen Aufruf kosten);
  auf `write`/`read` bleiben sie exakt (Zielaktion bzw. „nicht mit Schrot schießen"). **v2- und
  v3-Bundles sind nicht vergleichbar** — `compare_bundles` verweigert gemischte `pack_version`, Resume
  ohnehin (Pack-Hash). Alle Hetzner-tools-Zahlen aus v2 sind damit Altlast und gehören nicht in einen
  Bericht.
- **Das Tools-Pack (`touchstone tools`, `packs/opencode-tools.yaml`) wird nie gejudged.** Strukturierter
  Output ist mechanisch prüfbar; ein Judge brächte dort nur Rauschen. Die Tool-Schemas sind **Daten im
  Pack**, aus dem opencode-Binary 1.18.31 übernommen (bash dort `{command, timeout?, workdir?}`, *kein*
  `description`); das read-Ausgabeformat (`N: text`) ebenso — Edit-Items bekommen die Fixture als
  vorangegangenes read-Ergebnis. `calls` zählt nur **vollständige** Calls (arguments parsen zu einem
  Objekt): LM Studio liefert einen am Budget abgeschnittenen Call mit Namen, aber leerem `arguments` aus
  (der „Missing key at [content]"-Fall) — der darf das Soll nicht füllen. Code-Checks führen
  **unbeaufsichtigt Modell-Code** aus: `sandbox-exec` (kein Netz, Schreiben nur im Temp-Dir), leeres
  Env, kein stdin, eigene Prozessgruppe (SIGKILL nach 20 s); Python-Code wird per `runpy` als Modul
  **nicht** `__main__` geladen (ein Demo-`__main__`-Block darf nicht laufen). Braucht das Pack `node`
  und fehlt es, bricht `tools` vorab ab — sonst bestünden JS-Items ungeprüft. **Transportfehler sind
  kein Modellbefund:** nach 3 in Folge bricht der Lauf ab (`ToolsAborted`), `--resume` wiederholt die
  Fehler-Zellen (letzte Zeile je Zelle gilt). **Der Exit-Code trägt das mit:** `tools` endet mit **1**,
  sobald auch nur *eine* Fehler-Zelle offen ist — nicht erst beim Abbruch. Verstreute Fehler (unter 3
  in Folge) liefen vorher mit rc=0 durch, und die Schlusszeile zählte sie als „nicht bestanden", also
  wie ein gemessenes Scheitern; ein Treiberskript konnte „Modell fiel durch" nicht von „nie gemessen"
  unterscheiden (CORE-TEST-19). Die Quote zählt jetzt nur **gemessene** Zellen, die offenen stehen als
  eigene rote Zeile mit `--resume`-Hinweis daneben. `tools-compare` wertet Fehler-Paare nicht und lehnt
  Bundles mit mehr als einem Modell ab; Resume verlangt denselben Pack-Hash (inkl. Kontext). Unbekannte
  Argument-Keys werden **gezählt, nicht bestraft** (opencode ignoriert sie vermutlich); Edits prüfen
  **exakt** — strenger als opencodes 9 Fallback-Strategien, ein Beinahe-Treffer steht im Detail. Jede Prüfung ist in `tests/test_toolbench.py` gegen eine
  Referenzantwort (muss bestehen) **und** eine kaputte (muss scheitern) abgesichert — wer ein Item
  ändert, pflegt beide mit.
  Die Langkontext-Items **L1–L3** sind per YAML-Anker identisch mit M1/E3/C1, bekommen aber vorher ~50k
  Token Vorkontext (12 Module als read-Ergebnisse aus `packs/opencode-tools-context/`, ein **eingefrorener**
  Schnappschuss als `.py.txt` — nie nachziehen, sonst sind Läufe untereinander unvergleichbar). Obergrenze:
  8bit-JIT lädt mit 131072 Kontext, Vorkontext + 32k Budget muss darunter bleiben (Test prüft die Größe).
