import os
import csv
import datetime
import requests
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.data.enums import DataFeed

def load_config(filepath="config.csv"):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Configuration file not found: {filepath}")

    rows = []
    with open(filepath, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)
    return rows, fieldnames

def save_config(rows, fieldnames, filepath="config.csv"):
    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def parse_active(val) -> bool:
    if isinstance(val, bool):
        return val
    if not val:
        return False
    val_str = str(val).strip().lower()
    return val_str in ("true", "1", "yes", "active")

def parse_limit(val):
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        print(f"Invalid limit value: '{val}'. Ignored.")
        return None

def parse_datetime(val):
    if not val or str(val).strip() == "":
        return None
    try:
        val_str = str(val).strip()
        if val_str.endswith("Z"):
            val_str = val_str[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(val_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception as e:
        print(f"Error parsing datetime value '{val}': {e}")
        return None

def send_telegram_msg(message: str, token: str, chat_id: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def is_market_open(trading_client) -> bool:
    """Checks whether the US market is currently open."""
    try:
        clock = trading_client.get_clock()
        return clock.is_open
    except Exception as e:
        print(f"Error fetching market clock: {e}")
        return False

def fetch_latest_trades(data_client, tickers, feed: DataFeed = DataFeed.IEX):
    prices = {}
    failed_tickers = []

    # Try fetching as a batch first
    try:
        request_params = StockLatestTradeRequest(symbol_or_symbols=tickers, feed=feed)
        latest_trades = data_client.get_stock_latest_trade(request_params)
        for ticker in tickers:
            if latest_trades and ticker in latest_trades:
                prices[ticker] = float(latest_trades[ticker].price)
            else:
                failed_tickers.append(ticker)
    except Exception as batch_error:
        print(f"Batch query failed: {batch_error}. Switching to single queries.")
        # Fallback to single queries
        for ticker in tickers:
            try:
                request_params = StockLatestTradeRequest(symbol_or_symbols=[ticker], feed=feed)
                latest_trades = data_client.get_stock_latest_trade(request_params)
                if latest_trades and ticker in latest_trades:
                    prices[ticker] = float(latest_trades[ticker].price)
                else:
                    failed_tickers.append(ticker)
            except Exception as single_error:
                print(f"Error loading {ticker}: {single_error}")
                failed_tickers.append(ticker)

    return prices, failed_tickers

def run_monitor(config_path="config.csv"):
    load_dotenv()

    alpaca_key = os.getenv("ALPACA_API_KEY")
    alpaca_secret = os.getenv("ALPACA_SECRET_KEY")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not all([alpaca_key, alpaca_secret, telegram_token, telegram_chat_id]):
        missing = [
            k for k, v in {
                "ALPACA_API_KEY": alpaca_key,
                "ALPACA_SECRET_KEY": alpaca_secret,
                "TELEGRAM_BOT_TOKEN": telegram_token,
                "TELEGRAM_CHAT_ID": telegram_chat_id
            }.items() if not v
        ]
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")

    try:
        rows, fieldnames = load_config(config_path)
    except FileNotFoundError as e:
        print(e)
        return

    data_client = StockHistoricalDataClient(alpaca_key, alpaca_secret)
    trading_client = TradingClient(alpaca_key, alpaca_secret, paper=True)

    bypass_check = os.getenv("BYPASS_MARKET_OPEN_CHECK", "False").lower() in ("true", "1", "yes")
    if not bypass_check:
        if not is_market_open(trading_client):
            print("US Market is closed. Skipping execution.")
            return
    else:
        print("BYPASS_MARKET_OPEN_CHECK is active. Skipping market clock check.")

    active_rows = []
    active_tickers = []
    for idx, row in enumerate(rows):
        if parse_active(row.get("Active")):
            ticker = row.get("Ticker", "").strip()
            if ticker:
                active_rows.append((idx, row))
                active_tickers.append(ticker)

    if not active_tickers:
        print("No active tickers found.")
        return

    # Get configuration for Alpaca data feed (default to IEX for Free plan)
    feed_str = os.getenv("ALPACA_FEED", "IEX").upper()
    if feed_str == "SIP":
        feed = DataFeed.SIP
    elif feed_str == "DELAYED_SIP":
        feed = DataFeed.DELAYED_SIP
    elif feed_str == "OTC":
        feed = DataFeed.OTC
    else:
        feed = DataFeed.IEX

    prices, failed_tickers = fetch_latest_trades(data_client, active_tickers, feed=feed)

    for ticker in failed_tickers:
        err_msg = (
            f"⚠️ *Market Monitor Warning*\n\n"
            f"The price for ticker *{ticker}* could not be fetched from Alpaca. "
            f"Please verify the symbol."
        )
        send_telegram_msg(err_msg, telegram_token, telegram_chat_id)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cooldown_hours_str = os.getenv("COOLDOWN_HOURS", "2")
    try:
        cooldown_hours = float(cooldown_hours_str)
    except ValueError:
        cooldown_hours = 2.0

    any_updated = False

    for idx, row in active_rows:
        ticker = row.get("Ticker", "").strip()
        if ticker not in prices:
            continue

        current_price = prices[ticker]
        row["Last_Price"] = f"{current_price:.2f}"
        any_updated = True

        lower_limit = parse_limit(row.get("Lower_Limit"))
        upper_limit = parse_limit(row.get("Upper_Limit"))

        triggered = False
        signal_type = ""
        limit_broken = 0.0

        if lower_limit is not None and current_price <= lower_limit:
            triggered = True
            signal_type = "LOWER LIMIT BREACHED 📉"
            limit_broken = lower_limit
        elif upper_limit is not None and current_price >= upper_limit:
            triggered = True
            signal_type = "UPPER LIMIT BREACHED 📈"
            limit_broken = upper_limit

        if triggered:
            last_triggered_dt = parse_datetime(row.get("Last_Triggered"))
            should_alert = True

            if last_triggered_dt is not None:
                elapsed_seconds = (now_utc - last_triggered_dt).total_seconds()
                if elapsed_seconds < (cooldown_hours * 3600.0):
                    should_alert = False
                    print(f"Cooldown active for {ticker}. Last alert was {elapsed_seconds / 60.0:.1f} minutes ago.")

            if should_alert:
                # Convert timestamps to Eastern Time (ET) and German Time (MEZ/MESZ)
                now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
                now_met = now_utc.astimezone(ZoneInfo("Europe/Berlin"))

                tz_et_name = now_et.tzname() # e.g. "EDT" or "EST"
                tz_met_name = "MESZ" if now_met.tzname() == "CEST" else "MEZ"

                msg = (
                    f"🚨 *MARKET ALERT: {ticker}*\n\n"
                    f"Signal: *{signal_type}*\n"
                    f"Current Price: `${current_price:.2f}`\n"
                    f"Limit Triggered: `${limit_broken:.2f}`\n"
                    f"Timestamp (ET): `{now_et.strftime('%Y-%m-%d %H:%M:%S')} {tz_et_name}`\n"
                    f"Timestamp (DE): `{now_met.strftime('%Y-%m-%d %H:%M:%S')} {tz_met_name}`"
                )
                send_telegram_msg(msg, telegram_token, telegram_chat_id)
                row["Last_Triggered"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    if any_updated:
        save_config(rows, fieldnames, config_path)
        print("config.csv updated successfully.")

if __name__ == "__main__":
    try:
        run_monitor()
    except Exception as e:
        print(f"A critical error occurred: {e}")
