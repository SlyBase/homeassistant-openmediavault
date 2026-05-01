---
name: Recheck HA Release Stack
description: Prüft bei einer neuen Home-Assistant-Version, ob Python-, PHCC- und Test-Pins dieses Repos angehoben werden können und aktualisiert sie bei validierter Kompatibilität konsistent.
argument-hint: Ziel-Home-Assistant-Version, z. B. 2025.6 oder 2026.2
agent: agent
---

Prüfe für dieses Repository, ob der Stack auf die angegebene Home-Assistant-Version angehoben werden kann.

Arbeite immer gegen die aktuellen Dateien im Workspace und nutze insbesondere diese Referenzen:
- [pyproject.toml](../../pyproject.toml)
- [ci.yml](../workflows/ci.yml)
- [manifest.json](../../custom_components/omv/manifest.json)
- [hacs.json](../../hacs.json)
- [README.md](../../README.md)
- [info.md](../../info.md)
- [CHANGELOG.md](../../CHANGELOG.md)
- [dependabot.yml](../dependabot.yml)

Ziel:
- Finde den neuesten kompatiblen `pytest-homeassistant-custom-component`-Zweig für die gewünschte Home-Assistant-Version.
- Ermittle daraus die exakt dazugehörigen Versionen für `pytest`, `pytest-asyncio`, `pytest-cov` und weitere transitive Problemkandidaten wie `pycares` oder `aiodns`.
- Halte den Stack so aktuell wie möglich, aber nur innerhalb einer nachweislich funktionierenden und reproduzierbar validierten Kombination.

Vorgehen:
1. Lies die aktuelle Baseline des Repos aus den verlinkten Dateien.
2. Bestimme für die gewünschte Home-Assistant-Version den passenden PHCC-Release-Zweig statt blind auf die neueste Version zu gehen.
3. Leite daraus die tatsächlich erzwungenen Upstream-Pins ab.
4. Prüfe, ob der Stack in einer frischen Python-Umgebung auflösbar ist.
5. Validiere mindestens:
   - editable install von `.[test]`
   - `pytest --version`
   - `pytest tests -q`
   - Ruff-Checks, wenn sich der Python-Target-Stand ändert oder neue Lint-Konflikte sichtbar werden
6. Wenn die gewünschte Home-Assistant-Version kompatibel ist, aktualisiere alle betroffenen Dateien konsistent.
7. Wenn sie nicht kompatibel ist, ändere keine Dateien und erkläre präzise, welche Version den Fortschritt blockiert.

Konsistenzregeln bei erfolgreicher Anhebung:
- Aktualisiere nicht nur [pyproject.toml](../../pyproject.toml), sondern auch alle betroffenen Metadaten und Doku-Dateien.
- Achte darauf, dass Python-Minimum, CI-Python, HACS-Mindestversion und Home-Assistant-Mindestversion logisch zusammenpassen.
- Dokumentiere harte Kompatibilitätspins knapp, aber konkret, wenn sie wegen Upstream-Problemen nötig sind.
- Füge keine rein spekulativen Upgrades ein.

Erwartetes Ergebnisformat:

## Entscheidung
- Upgrade möglich oder nicht möglich
- Ziel-HA-Version
- Gewählter PHCC-Pin

## Kompatibler Stack
- Python
- Home Assistant
- pytest-homeassistant-custom-component
- pytest
- pytest-asyncio
- pytest-cov
- weitere relevante Pins mit Begründung

## Validierung
- Welche Befehle liefen
- Welche Ergebnisse kamen heraus

## Geänderte Dateien
- Nur falls eine validierte Anhebung erfolgreich war

## Risiken
- Offene Unsicherheiten oder bewusst beibehaltene Pins

Wenn die gewünschte Zielversion nur mit einem neueren Home-Assistant-Hauptzweig möglich ist, benenne das explizit, statt stillschweigend auf einen anderen HA-Zweig zu springen.