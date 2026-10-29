# Auftrag: Kosten-App fürs iPhone und iPad (ersetzt die Kosten-Konsole)

Stand: 2026-10-29. Für eine Claude-Code-Sitzung (Cloud) im Repo `JoFHub/llm-core`. Entscheidungen des Nutzers sind
markiert; alles andere ist Vorschlag und darf begründet abweichen (Abweichungen im PR nennen).

## Ziel

Eine **kleine Web-App**, mit der der Nutzer auf dem iPhone oder iPad in wenigen Sekunden sieht, was seine
Sprachmodelle kosten — **nur in Euro** —, und mit der er einfache Verwaltungsaufgaben erledigt (Limit ändern, Key
sperren, Key anlegen). Alles Weitere (Diagramme, Verläufe, Kosten je Modell) steht in den Konsolen der Anbieter;
die App verlinkt dorthin.

## Entscheidungen (Nutzer, 2026-10-29)

1. **Nur Euro.** Keine Dollar-Beträge in der App. Umrechnung zum Tageskurs, ungefähr reicht.
2. **Wenig Information.** Keine Diagramme, keine Tabellen mit vielen Spalten, keine Kosten je Tag oder Modell —
   dafür die Konsolen der Anbieter (OpenRouter: <https://openrouter.ai/activity>, Anthropic:
   <https://platform.claude.com/usage>).
3. **Bedienung vor allem am iPhone und iPad**, am Rechner soll sie auch gehen.
4. **Betrieb auf dem Probatio-Server**, hinter der vorhandenen Anmeldung (wie heute die Konsole, s. unten).
5. **Trennung der Anbieter:** Memoria und Probatio laufen nur über OpenRouter (Kosten über die Keys beim Anbieter),
   Fabula nur direkt bei Anthropic und nur auf dem Server (Kosten aus Fabulas Kosten-DB dort). Die App zeigt die
   Anbieter getrennt, ohne Namen der Apps in der Übersicht (weitere Apps können dazukommen).
6. **Einfache Verwaltung ja**, wie in der heutigen Konsole: Limit ändern, Key aktivieren/deaktivieren, neuen Key
   anlegen — nur OpenRouter.

## Ausgangslage (Stand `main`, 2026-10-29)

- `llm_core/anbieter.py` (ohne Oberfläche, getestet) — **wiederverwenden, nicht neu schreiben**:
  - `OpenRouter()`: `konto()` (Guthaben, verbraucht, Rest), `keys()` / `alle_keys()` (`KeyStatus` mit Name,
    gekürzter Kennung, `id`, `aktiv`, `heute`, `monat`, `verbrauch_gesamt`, `limit`, `limit_reset`, `rest`,
    `fehler`), `limit_aendern(…)`, `key_aktivieren(…)`, `key_anlegen(…)` — Änderungen nur mit `bestaetigt=True`,
    Ergebnis `Aenderung` mit vorher/nachher; `klartext` eines neuen Keys genau einmal.
  - `Anthropic()`: API-Keys nur gekürzt, mit `ANTHROPIC_ADMIN_KEY` Kosten der Organisation (auf dem Server nicht
    eingerichtet).
  - `ampel()`, `anteil()`, `Schwellen.from_env()`; Zwischenspeicher 60 s (`cache_leeren()`); Keys aus `.env` oder
    keyring, nie im Klartext angezeigt.
- `llm_core/konsole/` — Streamlit-Konsole (zwei Seiten OpenRouter/Anthropic, Seitenleiste mit $ und ≈ €). Darin
  brauchbar: `daten.py` (`kosten_seit(dbs, seit, provider)`, `db_kandidaten()`, DB nur lesend),
  `_gemeinsam.eur_kurs()` (EZB-Kurs über frankfurter.dev, an Streamlit gebunden — für die App ohne Streamlit neu
  fassen), `ui.betrag(wert, waehrung)` (deutsches Zahlenformat).
- **Server** (IONOS, Alias `probatio-server`): Konsole als eigener Stack `~/llm-core/server/` (Projekt `llm-core`,
  Dienst `kosten`, Port 8510, kein Port nach außen) im Docker-Netz `probatio`. Caddy und Anmeldung kommen aus dem
  Probatio-Stack: `{$KOSTEN_HOST} { import geschuetzt kosten kosten:8510 }`, Adresse
  `https://kosten.31-70-152-107.sslip.io`, Benutzer `kosten`. Die Anmeldung setzt ein Cookie (`Secure; HttpOnly;
  SameSite=Lax`, 30 Tage). Keys in `server/.env` (`OPENROUTER_PROVISIONING_KEY`, `KOSTEN_HOST`). Kosten-DBs nur
  lesend eingebunden: `/kosten/fabula`, `/kosten/probatio-ta`, `/kosten/probatio-visibilia`
  (`LLM_CORE_KONSOLE_DBS`). Aktualisieren: `server/aktualisieren.sh` (holt `main`, baut, prüft Gesundheit und
  Anmeldeseite).

## Umfang

### 1. Übersicht (Startseite)

Eine Seite, die ohne Scrollen auf ein iPhone passt, wenn alles in Ordnung ist:

```
Kosten                                   ⟳
┌ OpenRouter ─────────────────────────────┐
│ heute                          0,05 €   │
│ Oktober                        3,86 €   │
│ Guthaben                      14,01 €   │
└─────────────────────────────────────────┘
┌ Anthropic ──────────────────────────────┐
│ heute                          0,44 €   │
│ Oktober                        2,46 €   │
└─────────────────────────────────────────┘
Keys                                     +
● Memoria            3,33 € · 37 %       ›
● Probatio-visibilia 0,32 € ·  4 %       ›
○ Fabula             gesperrt            ›
Stand 09:34 · 1 $ = 0,90 € (EZB, 28.10.)
```

- **OpenRouter:** heute und laufender Monat (Summe der Keys, Monatsname statt „Monat“), Rest des Guthabens.
- **Anthropic:** heute und laufender Monat, berechnet aus den eingebundenen Kosten-DBs (`provider = anthropic`,
  Zeitzone Europe/Berlin). Kein Wort „berechnet“ in der Übersicht; die Herkunft steht in der Info (s. 4).
- **Keys (OpenRouter):** eine Zeile je Key: Ampelpunkt, Name, Verbrauch im laufenden Limit-Zeitraum in €, Anteil
  am Limit; gesperrte Keys grau mit „gesperrt“. Antippen öffnet den Key (s. 2). Sortierung: Probleme zuerst, dann
  nach Verbrauch.
- **Hinweise nur bei Problemen**, oben und auffällig: Key ohne Limit (rot), Limit erreicht (rot), fast erreicht
  (orange, Schwelle wie `Schwellen.from_env()`), Anbieter nicht erreichbar (grau, mit kurzem Grund). Sonst keine.
- **Aktualisieren:** Knopf ⟳ (leert den Zwischenspeicher); Ziehen zum Aktualisieren ist nicht nötig.
- Links zu den Konsolen der Anbieter (klein, z. B. am Kopf jeder Karte).

### 2. Key ansehen und verwalten (nur OpenRouter)

- Seite je Key: Name, gekürzte Kennung, Status, Verbrauch heute / Monat / gesamt in €, Limit in € mit
  Zurücksetzen (täglich, wöchentlich, monatlich, nie).
- **Limit ändern** (Eingabe in €), **sperren / entsperren**, **neuer Key** (Name, Limit in €, Zurücksetzen).
- **Jede Änderung mit Bestätigung** (vorher → nachher, ein deutlicher Knopf), danach kurze Rückmeldung.
- **Neuer Key:** Klartext genau einmal anzeigen, mit Knopf „Kopieren“ und Hinweis, ihn sofort in die `.env` der
  App bzw. den Passwortmanager zu übernehmen; nie speichern, loggen oder erneut anzeigen.
- **Euro → Dollar:** OpenRouter führt Limits in USD. Eingabe in €, beim Speichern zum Tageskurs in USD umrechnen
  (auf Cent runden); angezeigt wird überall der Euro-Wert. Dass der €-Wert eines Limits mit dem Kurs leicht
  schwankt, ist in Ordnung (in der Info erwähnen). Ohne Kurs: Ändern gesperrt mit Hinweis, statt in Dollar zu
  fragen.
- Ohne Provisioning-Key: Verwaltung aus, Hinweis in der Info.

### 3. Euro-Kurs

- EZB-Referenzkurs über `https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR` (kostenlos, ohne Key),
  höchstens zweimal am Tag abfragen, im Prozess zwischenspeichern; `LLM_CORE_EUR_KURS` setzt einen festen Kurs.
- Ohne Kurs (Netz, Dienst weg): letzter bekannter Kurs, sonst ein deutlicher grauer Hinweis — **keine**
  Dollar-Beträge als Ersatz.
- Kurs und Datum klein in der Fußzeile.

### 4. Info (statt Hilfe-Texten auf den Seiten)

Eine kurze Info-Seite oder ein Ausklapper: woher die Zahlen kommen (OpenRouter je Key; Anthropic aus Fabulas
Kosten-DB auf dem Server = Token-Zahlen mal Preisliste in `cost_tracker`), Kurs, Ampel, Stand.

### 5. iPhone und iPad

- **Als Web-App auf den Home-Bildschirm:** Web-App-Manifest, `apple-touch-icon`, `display: standalone`,
  Statusleisten-Farbe; schlichtes Symbol (z. B. Euro-Zeichen) ohne Markenlogos.
- Anmeldung: Caddy leitet ohne Cookie zur Anmeldeseite (`/_anmeldung/`). Prüfen und im PR beschreiben, ob das in
  der Home-Bildschirm-App funktioniert (iOS hat dort einen eigenen Cookie-Speicher; einmal anmelden reicht dann für
  30 Tage). Pfade der App dürfen nicht mit `/_anmeldung/` kollidieren.
- iPhone: eine Spalte; iPad und Rechner: Karten nebeneinander, Keys darunter, Breite begrenzt (nicht über den
  ganzen Bildschirm gezogen).
- Touch-Ziele mindestens 44 × 44 px, Eingaben 16 px (sonst zoomt Safari), Ziffern tabellarisch, Beträge rechts
  bündig. Hell und dunkel nach Systemeinstellung (`prefers-color-scheme`), sichere Ränder (`env(safe-area-inset-*)`).
- **Schnell:** erste Anzeige unter 1 s, wenn die Anbieter-Antworten im Zwischenspeicher liegen; keine großen
  Bibliotheken, kein Build-Schritt.

## Technik (Vorschlag)

- **Kein Streamlit** (auf dem iPhone träge: Websocket, Neuaufbau bei jeder Aktion, nicht als Home-Bildschirm-App
  gedacht). Stattdessen eine kleine serverseitig gerenderte App: z. B. Starlette oder FastAPI + Jinja2, HTML-Formulare
  (POST) mit wenig JavaScript (Kopieren-Knopf, ggf. Bestätigung), eigenes CSS ohne Framework.
- Neues Paket z. B. `llm_core/kosten_app/`, Start `python -m llm_core.kosten_app [--port 8510] [--adresse …]`,
  Standard nur `localhost`; neues Extra in `pyproject.toml` (z. B. `app`). llm-core selbst bleibt ohne diese
  Abhängigkeiten nutzbar (Import nur im App-Paket).
- Logik ohne Web-Framework testbar halten (Werte für die Übersicht, Umrechnung, Hinweise als reine Funktionen).
- **Server:** gleicher Stack und gleiche Adresse wie die Konsole — `Dockerfile`, `server/docker-compose.yml`
  (Dienst `kosten`, Port 8510, Volumes, `LLM_CORE_KONSOLE_DBS` ggf. umbenennen und Doku anpassen),
  `server/env.example`, `server/aktualisieren.sh` (unverändert nutzbar, solange Dienst `kosten` und Gesundheits-
  Endpunkt bleiben; einen `/gesund`-Endpunkt anbieten und den Healthcheck darauf umstellen). An Caddy und Probatio
  ändert sich nichts.
- **Konsole ersetzen (Vorschlag):** Die Streamlit-Konsole (`llm_core/konsole/`, `seiten/`, Tests, README-Abschnitt,
  die nur von ihr genutzten Helfer in `ui.py` wie `kurzstatus_werte`/`kurzstatus_hinweise`) entfällt, wenn die App
  alles aus „Umfang“ kann — eine Stelle statt zwei. `ui.kosten_seitenleiste*` (Bausteine der Apps) bleiben.

## Sicherheit

- Kein Key verlässt den Server-Prozess: nicht im HTML, nicht in URLs, nicht in Fehlermeldungen oder Logs; Keys nur
  gekürzt (`sk-or-v1-…a1b2`). Einzige Ausnahme: Klartext eines neuen Keys, genau einmal.
- Ändernde Aktionen nur per POST; dazu ein Schutz gegen untergeschobene Anfragen (CSRF-Token je Sitzung oder je
  Formular) — das Cookie der Anmeldung (`SameSite=Lax`) allein reicht nicht als Begründung, im PR kurz bewerten.
- Bestätigung serverseitig erzwingen (die Aktion läuft erst mit dem bestätigten Plan, nicht schon beim ersten POST);
  `anbieter`-Methoden nur mit `bestaetigt=True` nach dieser Bestätigung.
- Eingaben prüfen (Limit ≥ 0 und mit Obergrenze, z. B. 1000 €; Name: Länge, erlaubte Zeichen).
- Ohne eigene Anmeldung: lokal nur `localhost`, auf dem Server nur hinter Caddy (kein Port nach außen). Im Image
  keine Keys (`.dockerignore` beibehalten).
- Sicherheits-Header (z. B. `Content-Security-Policy` ohne fremde Quellen, `X-Content-Type-Options`,
  `Referrer-Policy: no-referrer`), Cache-Header so, dass Beträge und Klartext-Key nicht im Browser-Cache landen.

## Tests

- Ohne Netz und ohne echte Keys: Anbieter über den vorhandenen `transport`-Parameter mocken (Muster in
  `tests/test_anbieter.py`), Kurs gemockt, Kosten-DBs als SQLite in `tmp_path`. `tests/conftest.py` isoliert die
  Kosten-DB je Test — so lassen.
- Abdecken: Übersicht (Werte, nur €, kein „$“ im HTML), Hinweise nur bei Problemen, Umrechnung € → USD beim Ändern,
  Bestätigung nötig (ohne Bestätigung kein Aufruf beim Anbieter), Klartext nur einmal, CSRF abgewiesen, keine
  vollständigen Keys im HTML, ohne Provisioning-Key, ohne Kurs, Anbieter-Fehler (401, Timeout), `/gesund`.
- `pytest` grün; Docker-Image baut und startet lokal, `/gesund` antwortet.
- **Nicht in der Cloud:** Läufe gegen echte Anbieter und das Ausrollen auf den Server — das macht der Nutzer mit
  einer lokalen Sitzung (SSH-Zugang nur auf dem Omen).

## Nicht in diesem Auftrag

- Diagramme, Verläufe, Kosten je Tag/Modell/Zuordnung, Auswahl der Kosten-DB, Zeitraum-Optionen.
- Verwaltung bei Anthropic (Ausgabengrenze und Keys in der Anthropic Console).
- Umstellung der Kosten-Bausteine in Probatio, Memoria, Fabula auf Euro (eigene Sitzungen in den App-Repos,
  falls gewünscht).
- Push-Benachrichtigungen, eigene Anmeldung, mehrere Benutzer.

## Abgabe

- Branch und Pull Request gegen `main`: was gebaut ist, was getestet ist und was nicht, Abweichungen vom Vorschlag,
  offene Fragen. Bildschirmfotos der Übersicht in iPhone- und iPad-Breite (z. B. aus einem Headless-Browser).
- Version in `pyproject.toml` anheben (0.3.0), README-Abschnitt „Kosten-App“ (Start lokal, Server, Einrichtung als
  Home-Bildschirm-App) statt „Konsole“.
- Für das Ausrollen eine kurze Schrittfolge im PR (auf dem Server: `bash ~/llm-core/server/aktualisieren.sh`, ggf.
  geänderte Variablen in `server/.env`), danach Klicktest des Nutzers am iPhone.
