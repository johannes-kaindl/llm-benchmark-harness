# Decisions — Warum es so ist

Aus der `AGENTS.md` ausgelagert (CORE-AGENT-15). Die ADRs zum Produkt stehen unter `docs/decisions/`; hier steht das Warum der Repo-Verwaltung.

## Hosting: Spiegel und Push-Praxis

- **Kein GitHub-Mirror mehr (2026-09-05).** Der `github`-Remote ist entfernt. GitHub war als
  Backup-Gegenstelle gedacht; diese Rolle trägt seit 2026-07 `git.jkaindl.de`, und seit dem
  2026-08-30 ist das Konto ohnehin geflaggt (anonym 404, Actions kontoweit aus). Bleiben soll
  GitHub nur, wo es funktional erzwungen ist — die Obsidian-Store-Kette; dieses Repo ist kein
  Plugin. Der Absatz darunter bleibt als **Begründung** stehen, warum ein Mirror, solange es
  ihn gab, von Hand gepusht werden musste.
- ⚠️ **Beide Remotes explizit pushen — der Mirror trägt nicht.** Bis 2026-07-28 stand hier, ein
  Forgejo→GitHub-Push-Mirror (`sync_on_commit`) ziehe GitHub automatisch nach. Gemessen war das
  falsch: GitHub hing **11 Commits** zurück (die gesamte `docs/decisions/`-Ebene *und* Release
  `0.2.0`), und ein `origin`-Push zog auch danach nicht nach (zweimal geprüft, sofort und nach
  20 s). Also immer:

  ```bash
  git push origin main && git push github main
  # Die Forges selbst messen, nicht die lokalen Tracking-Refs (die koennen stale sein):
  git rev-parse main
  git ls-remote --heads origin main
  git ls-remote --heads github main      # drei identische Hashes = fertig
  ```

  (`git rev-parse --short a b c` bricht mit „Needed a single revision" ab — `--short` nimmt nur
  **eine** Revision. Ohne `--short` funktioniert die Mehrfach-Form, misst aber eben nur die
  lokalen Kopien.)

  Die Zusage bleibt hier als Warnung stehen, statt gelöscht zu werden: sie hat elf Commits lang
  verdeckt, dass die öffentliche Seite veraltet war. Wird der Mirror je repariert, gehört das
  **an den Remotes gemessen**, nicht in dieser Datei behauptet.
