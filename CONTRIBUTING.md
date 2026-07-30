# Contributing

Danke fürs Mitwirken! Dieses Repo folgt der Workspace-Leitkonvention
(`../_docs/CONVENTIONS.md`). Kurzfassung:

## Branch-Modell (CORE-GIT-02)
- `main` ist immer grün. Feature-Arbeit in `feat/<name>`-Branches, Merge via `git merge --no-ff`.
- Direkt-Push auf `main` nur mit expliziter Autorisierung.

## Commits (CORE-GIT-04/05/06)
- **Conventional Commits:** `feat|fix|docs|chore|refactor|test(scope): …` (Beschreibung darf Deutsch sein).
- Bei substanziellem AI-Beitrag Trailer anfügen:
  `Co-Authored-By: Claude Opus <Version> (1M context) <noreply@anthropic.com>`
- **Nur berührte Dateien stagen — nie `git add -A`.**

## Tags & Remotes (CORE-GIT-01/03)
- SemVer **ohne** v-Präfix (`1.2.3`).
- Primär-Remote ist git.jkaindl.de (`origin`); GitHub nur Mirror.

## Qualität vor dem Commit
- Tests grün, Linter/Typecheck sauber (siehe Profil-Regeln der Leitkonvention).
- Wo vorhanden: pre-commit-Hooks nicht mit `--no-verify` umgehen.

## Lizenz der Beiträge (CORE-META-07/08)
- Code unter **AGPL-3.0**; mit deinem Beitrag akzeptierst du das [CLA](CLA.md).
- Dokumentation/Texte unter **CC BY-SA 4.0**.
