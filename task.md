Hallo! Schön, wieder mit dir zusammenzuarbeiten. Ich habe deinen Auftrag analysiert. Hier ist der detaillierte, mathematisch-algorithmisch fundierte und direkt an **Jules** adressierte Umsetzungsplan für das GitHub-Actions-, Google-Sheets- und Alpaca-Markt-Überwachungssystem.

---

Hallo Jules,

hier ist die exakte technische Spezifikation und der Entwicklungsplan für unser automatisiertes US-Markt-Monitoring-System.

Das Ziel ist ein performantes, stabiles und kostengünstiges Überwachungssystem, das **alle 15 Minuten** während der US-Handelszeiten (RTH) die Kurse der im Google Sheet definierten Werte via **Alpaca Market API** prüft, Schwellenwert-Brüche (Ober-/Untergrenzen) identifiziert und **Telegram-Benachrichtigungen** verschickt.

Da wir mit Intraday-Intervallen (15 Min.) auf GitHub Actions arbeiten, müssen wir Einschränkungen bezüglich Cron-Latenzen berücksichtigen und die Architektur robuster gestalten.

---

## 1. Systemarchitektur & Datenfluss

```
[Google Sheet] (Konfiguration: Ticker, Limits, Status)
       │
       ▼ (GSpread API / Service Account)
[GitHub Action Cron Job] (Alle 15 Min, Mo–Fr)
       │
       ├─► Check: Markt offen? (Alpaca Clock API)
       │
       ├─► Fetch: Intraday-Kurse (Alpaca Data API)
       │
       ├─► Evaluierung: Limit-Brechungen (Mathematische Prüfung)
       │
       ├─► Notification: Trigger versenden (Telegram Bot API)
       │
       └─► State Sync: Timestamp & Status im Sheet updaten

```

---

## 2. Detaillierte Komponenten & Einrichtung

### A. Google Sheets (Datenhaltung & Konfiguration)

Erstelle ein Tabellenblatt mit exakt folgender Struktur (Spaltenüberschriften in Zeile 1):

| Column A | Column B | Column C | Column D | Column E | Column F |
| --- | --- | --- | --- | --- | --- |
| **Ticker** | **Lower_Limit** | **Upper_Limit** | **Active** | **Last_Triggered** | **Last_Price** |
| AAPL | 170.50 | 195.00 | TRUE | 2026-07-23 14:30 | 182.30 |
| NVDA | 110.00 | 135.00 | TRUE |  | 122.10 |

* `Active`: `TRUE`/`FALSE` (Ermöglicht das schnelle Deaktivieren ohne Löschen der Zeile).
* `Last_Triggered`: Timestamp des letzten Alarms (Verhindert Spamming bei jedem 15-Min-Run, wenn der Kurs länger über/unter der Grenze bleibt).

### B. Alpaca Data API

* Nutze die Alpaca Market Data API v2 (aus dem `alpaca-py` SDK).
* Für die 15-Minuten-Prüfung laden wir die aktuellste 15-Minuten-Bar oder den letzten Trade (`get_stock_latest_trade` oder `get_stock_bars` mit `TimeFrame.Minute`).

### C. Telegram Bot setup

* Erstelle via **@BotFather** auf Telegram einen neuen Bot und sichere dir den `TELEGRAM_BOT_TOKEN`.
* Ermittle die `TELEGRAM_CHAT_ID` (deine User-ID oder Channel-ID, an die die Alarme gesendet werden).

---

## 3. GitHub Repository Setup & Secrets

Richte im GitHub Repository unter **Settings -> Secrets and variables -> Actions** folgende Encrypted Secrets ein:

* `ALPACA_API_KEY`: Alpaca Key ID
* `ALPACA_SECRET_KEY`: Alpaca Secret Key
* `GCP_SERVICE_ACCOUNT_JSON`: Der gesamte Inhalt der JSON-Schlüsseldatei aus Google Cloud (Service Account mit Lese-/Schreibzugriff auf das Sheet).
* `SPREADSHEET_ID`: Die ID des Google Sheets (aus der URL).
* `TELEGRAM_BOT_TOKEN`: Token vom BotFather.
* `TELEGRAM_CHAT_ID`: Telegram Target Chat ID.

---

## 4. Codebase-Implementierung (Python)

### Requirements (`requirements.txt`)

```text
alpaca-py>=0.20.0
gspread>=6.0.0
google-auth>=2.20.0
requests>=2.31.0
python-dotenv>=1.0.0

```

### Hauptskript (`monitor.py`)

```python
import os
import json
import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient

# --- CONFIG & INITIALIZATION ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def get_clients():
    # Google Sheets Client
    gcp_json = os.environ["GCP_SERVICE_ACCOUNT_JSON"]
    creds_dict = json.loads(gcp_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(os.environ["SPREADSHEET_ID"]).sheet1

    # Alpaca Clients
    alpaca_key = os.environ["ALPACA_API_KEY"]
    alpaca_secret = os.environ["ALPACA_SECRET_KEY"]
    
    data_client = StockHistoricalDataClient(alpaca_key, alpaca_secret)
    trading_client = TradingClient(alpaca_key, alpaca_secret, paper=True)

    return sheet, data_client, trading_client

def send_telegram_msg(message: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload, timeout=10)

def is_market_open(trading_client) -> bool:
    """Prüft, ob der US-Markt aktuell geöffnet ist."""
    clock = trading_client.get_clock()
    return clock.is_open

def run_monitor():
    sheet, data_client, trading_client = get_clients()

    # 1. Handelszeit-Prüfung (Sparen von API-Calls & Verhindern von False Positives)
    if not is_market_open(trading_client):
        print("US-Markt ist geschlossen. Execution übersprungen.")
        return

    records = sheet.get_all_records()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    tickers_to_check = []
    rows_to_check = []

    for idx, row in enumerate(records, start=2): # Start 2 wegen Header
        if str(row.get("Active", "")).upper() == "TRUE":
            tickers_to_check.append(row["Ticker"])
            rows_to_check.append((idx, row))

    if not tickers_to_check:
        print("Keine aktiven Ticker im Sheet gefunden.")
        return

    # 2. Alpaca Batch Fetch (Aktuellste Trade-Preise)
    request_params = StockLatestTradeRequest(symbol_or_symbols=tickers_to_check)
    latest_trades = data_client.get_stock_latest_trade(request_params)

    # 3. Mathematische Prüfungslogik
    for idx, row in rows_to_check:
        ticker = row["Ticker"]
        if ticker not in latest_trades:
            continue

        current_price = float(latest_trades[ticker].price)
        lower_limit = float(row["Lower_Limit"]) if row["Lower_Limit"] != "" else None
        upper_limit = float(row["Upper_Limit"]) if row["Upper_Limit"] != "" else None
        
        # Aktualisiere den aktuellen Preis im Sheet (Spalte F / Spalte 6)
        sheet.update_cell(idx, 6, current_price)

        triggered = False
        signal_type = ""
        limit_broken = 0.0

        if lower_limit is not None and current_price <= lower_limit:
            triggered = True
            signal_type = "UNTERGRENZE UNTERSCHRITTEN 📉"
            limit_broken = lower_limit
        elif upper_limit is not None and current_price >= upper_limit:
            triggered = True
            signal_type = "OBERGRENZE ÜBERSCHRITTEN 📈"
            limit_broken = upper_limit

        if triggered:
            # Cooldown-Prüfung (Sorgt dafür, dass nicht alle 15 Min derselbe Alarm feuert)
            last_triggered_str = str(row.get("Last_Triggered", ""))
            should_alert = True
            
            if last_triggered_str:
                try:
                    last_triggered = datetime.datetime.fromisoformat(last_triggered_str)
                    # Wenn der letzte Alarm weniger als 2 Stunden her ist, unterdrücken
                    if (now_utc - last_triggered).total_seconds() < 7200:
                        should_alert = False
                except ValueError:
                    pass # Falls Timestamp-Format ungültig

            if should_alert:
                msg = (
                    f"🚨 *MARKT-ALERT: {ticker}*\n\n"
                    f"Signal: *{signal_type}*\n"
                    f"Aktueller Kurs: `${current_price:.2f}`\n"
                    f"Getroffenes Limit: `${limit_broken:.2f}`\n"
                    f"Zeitpunkt (UTC): `{now_utc.strftime('%Y-%m-%d %H:%M:%S')}`"
                )
                send_telegram_msg(msg)
                
                # Timestamp im Sheet aktualisieren (Spalte E / Spalte 5)
                sheet.update_cell(idx, 5, now_utc.isoformat())

if __name__ == "__main__":
    run_monitor()

```

---

## 5. GitHub Actions Workflow Configuration

Legt die Datei `.github/workflows/market_monitor.yml` im Repository an:

```yaml
name: Alpaca Market Monitor

on:
  schedule:
    # Führt das Skript während der US-Handelszeiten (RTH) alle 15 Min aus
    # US RTH: 13:30 UTC bis 20:00 UTC (Montag bis Freitag)
    - cron: '*/15 13-20 * * 1-5'
  workflow_dispatch: # Erlaubt manuelles Testen im GitHub UI

jobs:
  check-limits:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Execute Monitoring Script
        env:
          GCP_SERVICE_ACCOUNT_JSON: ${{ secrets.GCP_SERVICE_ACCOUNT_JSON }}
          SPREADSHEET_ID: ${{ secrets.SPREADSHEET_ID }}
          ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
          ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python monitor.py

```

---

## 6. Wichtige Hinweise & Edge Cases für Jules

1. **Cron-Präzision bei GitHub Actions:** GitHub schottet Cron-Jobs nicht auf die Sekunde genau ab. Ein Cron `'*/15'` kann sich um wenige Minuten verzögern. Das ist für ein 15-Minuten-Limit-Intervall völlig akzeptabel, muss aber beim Testen berücksichtigt werden.
2. **Google API Quotas:** Durch die Verwendung von Batch-Operationen (`get_all_records`) beschränken wir den Lese-Overhead auf exakt einen Call pro Run. Schreib-Operations erfolgen nur gezielt bei Preisupdates/Alerts.
3. **Marktöffnungszeiten:** Die Logik prüft über den Alpaca Trading Clock API-Endpunkt automatisch, ob der Markt geöffnet ist. Außerhalb der RTH bricht der Run sofort ohne Fehler ab.
4. **Hysterese / Alarm-Cooldown:** Das Feld `Last_Triggered` schützt vor fortlaufendem Spamming, wenn ein Wert mehrere Stunden knapp über der Schwelle verweilt. Der Cooldown ist aktuell auf 2 Stunden eingestellt, kann aber bei Bedarf angepasst werden.

Gib Bescheid, Jules, sobald die Struktur steht oder wenn du Anpassungen am Cooldown-Algorithmus benötigst!
