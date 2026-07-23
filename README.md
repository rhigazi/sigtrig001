# Alpaca US-Markt Limit-Überwachungssystem (Market Monitor)

Ein performantes, stabiles und kostengünstiges Überwachungssystem, das alle 15 Minuten während der US-Handelszeiten (RTH) die Kurse der in `config.csv` definierten Werte via **Alpaca Market API** prüft, Schwellenwert-Brüche (Ober-/Untergrenzen) identifiziert und **Telegram-Benachrichtigungen** verschickt.

Der aktuelle Zustand (letzter Preis & Auslösezeitpunkt) wird automatisch wieder in der Datei `config.csv` im Repository gespeichert.

---

## Funktionsweise & Features

- **Automatisierte Ausführung:** Ein GitHub Actions Workflow führt das Skript alle 15 Minuten (Mo–Fr, 13:00 bis 21:00 UTC) aus, um die Handelszeiten ganzjährig abzudecken.
- **Fehlertoleranz:** Scheitert das Laden eines Tickers bei Alpaca, erhältst du eine Warnung über Telegram, und andere Werte werden normal weitergeprüft.
- **Alpaca Free-Plan Kompatibilität (IEX Feed):** Standardmäßig nutzt das Skript den **IEX**-Datenfeed (Investoren-Börse), welcher für kostenlose Alpaca-Konten (Free Plan / Paper Trading) freigeschaltet ist und keine `403 Forbidden` Fehler wirft. Bei bezahlten Konten kann flexibel auf den unlimitierten **SIP**-Feed umgestellt werden.
- **Hysterese & Cooldown:** Um Spam-Nachrichten zu vermeiden, gibt es einen standardmäßigen **2-Stunden-Cooldown** pro Ticker (über `COOLDOWN_HOURS` einstellbar).
- **Zustands-Synchronisierung:** Letzte Kurse (`Last_Price`) und Alarm-Zeitstempel (`Last_Triggered`) werden zurück ins Repository gepusht.
- **Wochenend-Schutz:** Das Skript bricht außerhalb der US-Marktzeiten automatisch ab, um API-Abfragen zu sparen.

---

## 1. Konfiguration (`config.csv`)

Die Konfiguration der Aktien und Limits erfolgt direkt in der Datei `config.csv` im Repository:

| Spalte | Beschreibung |
|---|---|
| **Ticker** | Das Aktiensymbol (z. B. `AAPL`, `NVDA`). |
| **Lower_Limit** | Die Untergrenze. Fällt der Kurs darunter oder darauf, wird alarmiert. (Kann leer gelassen werden). |
| **Upper_Limit** | Die Obergrenze. Steigt der Kurs darüber oder darauf, wird alarmiert. (Kann leer gelassen werden). |
| **Active** | `TRUE` oder `FALSE` (Aktiviert oder deaktiviert die Prüfung für diesen Wert). |
| **Last_Triggered** | *(Wird automatisch befüllt)* Zeitstempel des letzten Alarms (UTC). |
| **Last_Price** | *(Wird automatisch befüllt)* Zuletzt abgefragter Kurs. |

**Beispiel `config.csv`:**
```csv
Ticker,Lower_Limit,Upper_Limit,Active,Last_Triggered,Last_Price
AAPL,170.50,195.00,TRUE,,
NVDA,110.00,135.00,TRUE,,
```

---

## 2. Einrichtung & Secrets konfigurieren

Damit das System vollautomatisch auf GitHub laufen kann, musst du folgende Secrets in deinem GitHub-Repository hinterlegen:

1. Gehe in deinem GitHub-Repository auf **Settings -> Secrets and variables -> Actions**.
2. Klicke auf **New repository secret** und füge die folgenden Secrets hinzu:

| Secret-Name | Beschreibung | Herkunft / Ermittlung |
|---|---|---|
| `ALPACA_API_KEY` | Alpaca Key ID | Dein Alpaca-Dashboard (Paper oder Live) |
| `ALPACA_SECRET_KEY` | Alpaca Secret Key | Dein Alpaca-Dashboard (Paper oder Live) |
| `TELEGRAM_BOT_TOKEN` | Token deines Telegram-Bots | Erstelle einen Bot via **@BotFather** auf Telegram |
| `TELEGRAM_CHAT_ID` | Deine Telegram-Chat- oder Gruppen-ID | Sende eine Nachricht an den Bot und rufe `https://api.telegram.org/bot<TOKEN>/getUpdates` auf, um deine `chat.id` zu finden. |

---

## 3. Lokale Entwicklung & Testen

Falls du das Skript lokal auf deinem Computer testen möchtest, folge diesen Schritten:

### A. Repository klonen & Abhängigkeiten installieren
```bash
# Virtuelle Umgebung erstellen (optional aber empfohlen)
python -m venv .venv
source .venv/bin/activate  # Unter Windows: .venv\Scripts\activate

# Abhängigkeiten installieren
pip install -r requirements.txt
```

### B. Umgebungsvariablen anlegen
Erstelle eine `.env`-Datei im Hauptverzeichnis des Projekts:
```env
ALPACA_API_KEY=dein_alpaca_key
ALPACA_SECRET_KEY=dein_alpaca_secret
TELEGRAM_BOT_TOKEN=dein_telegram_token
TELEGRAM_CHAT_ID=deine_chat_id

# Daten-Feed Konfiguration (Standard: IEX für Free Plan; SIP für unlimitierte Paid Plans)
ALPACA_FEED=IEX

# Für lokales Testen am Wochenende oder außerhalb der US-Handelszeiten:
BYPASS_MARKET_OPEN_CHECK=True

# Cooldown-Dauer in Stunden einstellen (Standard: 2 Stunden)
COOLDOWN_HOURS=2.0
```

### C. Skript ausführen
```bash
python monitor.py
```

### D. Unit Tests ausführen
Um sicherzustellen, dass die gesamte Alarm- und Parsing-Logik korrekt arbeitet, kannst du die Unit-Tests ausführen:
```bash
python -m unittest test_monitor.py
```

---

## 4. GitHub Actions & Handelszeiten (Cron)

Der Workflow in `.github/workflows/market_monitor.yml` ist bereits so vorkonfiguriert, dass er alle 15 Minuten während der Handelszeiten startet und das geänderte `config.csv` automatisch committet und pusht.

### Genaue Aufschlüsselung der Börsenzeiten:
Die **regulären US-Handelszeiten (RTH)** sind von **09:30 bis 16:00 Uhr Eastern Time (ET)**. Da GHA-Server standardmäßig in UTC laufen, verschieben sich die Zeiten wie folgt:

- **Sommerzeit (EDT, UTC-4):**
  - Handelszeit: **13:30 bis 20:00 Uhr UTC**
  - Deutsche Zeit (MESZ): **15:30 bis 22:00 Uhr** (da auch in Deutschland umgestellt wird)
- **Winterzeit (EST, UTC-5):**
  - Handelszeit: **14:30 bis 21:00 Uhr UTC**
  - Deutsche Zeit (MEZ): **15:30 bis 22:00 Uhr** (da auch in Deutschland umgestellt wird)

### Optimierte Ausführung (Vermeidung von GitHub-Verzögerungen)
GitHub Actions startet Cron-Jobs oft verspätet, wenn sie zu vollen Viertelstunden (`*/15` -> `00`, `15`, `30`, `45`) laufen, da die globale Serverlast dann am höchsten ist.

Daher ist unser Workflow auf **krumme Minuten** (`7`, `22`, `37`, `52`) eingestellt, um Verzögerungen in der Warteschlange drastisch zu minimieren:
```yaml
on:
  schedule:
    # Läuft zur Minute 7, 22, 37, 52 (deutlich weniger Serverlast auf GitHub)
    - cron: '7,22,37,52 13-21 * * 1-5'
```

### Optionale Lösung für 100% Pünktlichkeit (Externer Cron)
Wenn du absolut sekundengenaue und zuverlässige Trigger benötigst, kannst du den GitHub Scheduler umgehen und einen kostenlosen externen Dienst wie **cron-job.org** oder **UptimeRobot** verwenden:

1. Erstelle ein **Personal Access Token (PAT)** mit `workflow`-Berechtigung in deinem GitHub-Profil.
2. Richte bei dem externen Dienst einen Cron-Job ein, der alle 15 Minuten einen `POST`-Request an die GitHub API sendet, um das `workflow_dispatch`-Event deines Repositories auszulösen:

```http
POST https://api.github.com/repos/DEIN_GITHUB_USER/DEIN_REPO_NAME/actions/workflows/market_monitor.yml/dispatches
Headers:
  Authorization: Bearer DEIN_GITHUB_PAT
  Accept: application/vnd.github+json
Body (JSON):
  { "ref": "main" }
```
Dadurch startet der Workflow jedes Mal pünktlich auf die Sekunde genau!
