import os
import unittest
from unittest.mock import patch, MagicMock
import datetime
import monitor
from alpaca.data.enums import DataFeed

class TestMonitor(unittest.TestCase):

    def test_parse_active(self):
        self.assertTrue(monitor.parse_active("true"))
        self.assertTrue(monitor.parse_active("TRUE"))
        self.assertTrue(monitor.parse_active(True))
        self.assertTrue(monitor.parse_active("1"))
        self.assertTrue(monitor.parse_active("yes"))
        self.assertTrue(monitor.parse_active("active"))

        self.assertFalse(monitor.parse_active("false"))
        self.assertFalse(monitor.parse_active(False))
        self.assertFalse(monitor.parse_active(""))
        self.assertFalse(monitor.parse_active(None))
        self.assertFalse(monitor.parse_active("no"))

    def test_parse_limit(self):
        self.assertEqual(monitor.parse_limit("170.50"), 170.50)
        self.assertEqual(monitor.parse_limit(110), 110.0)
        self.assertIsNone(monitor.parse_limit(""))
        self.assertIsNone(monitor.parse_limit(None))
        self.assertIsNone(monitor.parse_limit("abc"))

    def test_parse_datetime(self):
        dt = monitor.parse_datetime("2026-07-23T14:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)

        # Test offset
        dt_offset = monitor.parse_datetime("2026-07-23T14:30:00+00:00")
        self.assertIsNotNone(dt_offset)
        self.assertEqual(dt_offset.hour, 14)

        # Test empty/none
        self.assertIsNone(monitor.parse_datetime(""))
        self.assertIsNone(monitor.parse_datetime(None))

    @patch("monitor.load_dotenv")
    def test_run_monitor_missing_env(self, mock_load_dotenv):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                monitor.run_monitor()

    @patch("monitor.TradingClient")
    @patch("monitor.StockHistoricalDataClient")
    @patch("monitor.load_config")
    @patch("monitor.save_config")
    @patch("monitor.send_telegram_msg")
    @patch("monitor.is_market_open")
    def test_run_monitor_market_closed(self, mock_is_market_open, mock_send, mock_save, mock_load, mock_hist, mock_trade):
        mock_is_market_open.return_value = False
        rows = [
            {"Ticker": "AAPL", "Lower_Limit": "170.50", "Upper_Limit": "195.00", "Active": "TRUE", "Last_Triggered": "", "Last_Price": ""}
        ]
        mock_load.return_value = (rows, list(rows[0].keys()))

        env = {
            "ALPACA_API_KEY": "fake_key",
            "ALPACA_SECRET_KEY": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "fake_chat_id",
            "BYPASS_MARKET_OPEN_CHECK": "False"
        }

        with patch.dict(os.environ, env, clear=True):
            monitor.run_monitor()

        mock_is_market_open.assert_called_once()
        # Should not save config or fetch trades
        mock_save.assert_not_called()

    @patch("monitor.TradingClient")
    @patch("monitor.StockHistoricalDataClient")
    @patch("monitor.load_config")
    @patch("monitor.save_config")
    @patch("monitor.send_telegram_msg")
    @patch("monitor.is_market_open")
    def test_run_monitor_market_open_with_alerts(self, mock_is_market_open, mock_send, mock_save, mock_load, mock_hist, mock_trade):
        mock_is_market_open.return_value = True

        # Setup config
        rows = [
            {"Ticker": "AAPL", "Lower_Limit": "170.50", "Upper_Limit": "195.00", "Active": "TRUE", "Last_Triggered": "", "Last_Price": ""},
            {"Ticker": "NVDA", "Lower_Limit": "110.00", "Upper_Limit": "135.00", "Active": "TRUE", "Last_Triggered": "", "Last_Price": ""},
            {"Ticker": "TSLA", "Lower_Limit": "150.00", "Upper_Limit": "250.00", "Active": "FALSE", "Last_Triggered": "", "Last_Price": ""}
        ]
        mock_load.return_value = (rows, list(rows[0].keys()))

        # Setup alpaca client responses
        mock_data_inst = MagicMock()
        mock_hist.return_value = mock_data_inst

        trade_aapl = MagicMock()
        trade_aapl.price = 165.0  # AAPL: Lower limit breached (165.0 <= 170.50)

        trade_nvda = MagicMock()
        trade_nvda.price = 140.0  # NVDA: Upper limit breached (140.0 >= 135.00)

        # Mock get_stock_latest_trade
        mock_data_inst.get_stock_latest_trade.return_value = {
            "AAPL": trade_aapl,
            "NVDA": trade_nvda
        }

        env = {
            "ALPACA_API_KEY": "fake_key",
            "ALPACA_SECRET_KEY": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "fake_chat_id",
            "BYPASS_MARKET_OPEN_CHECK": "False",
            "ALPACA_FEED": "IEX"
        }

        with patch.dict(os.environ, env, clear=True):
            monitor.run_monitor()

        mock_is_market_open.assert_called_once()
        self.assertEqual(mock_send.call_count, 2)

        # Verify that get_stock_latest_trade was called with feed=DataFeed.IEX
        call_arg = mock_data_inst.get_stock_latest_trade.call_args[0][0]
        self.assertEqual(call_arg.feed, DataFeed.IEX)

        # Verify rows was updated
        self.assertEqual(rows[0]["Last_Price"], "165.00")
        self.assertIsNotNone(rows[0]["Last_Triggered"])
        self.assertEqual(rows[1]["Last_Price"], "140.00")
        self.assertIsNotNone(rows[1]["Last_Triggered"])
        # TSLA is inactive, so no price or last_triggered update
        self.assertEqual(rows[2]["Last_Price"], "")

        # Verify mock_save was called
        mock_save.assert_called_once()

    @patch("monitor.TradingClient")
    @patch("monitor.StockHistoricalDataClient")
    @patch("monitor.load_config")
    @patch("monitor.save_config")
    @patch("monitor.send_telegram_msg")
    def test_run_monitor_bypass_market_check_and_cooldown(self, mock_send, mock_save, mock_load, mock_hist, mock_trade):
        # We set Last_Triggered to just 30 mins ago
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        thirty_mins_ago = now_utc - datetime.timedelta(minutes=30)
        thirty_mins_ago_str = thirty_mins_ago.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = [
            {"Ticker": "AAPL", "Lower_Limit": "170.50", "Upper_Limit": "195.00", "Active": "TRUE", "Last_Triggered": thirty_mins_ago_str, "Last_Price": ""}
        ]
        mock_load.return_value = (rows, list(rows[0].keys()))

        mock_data_inst = MagicMock()
        mock_hist.return_value = mock_data_inst

        trade_aapl = MagicMock()
        trade_aapl.price = 165.0  # AAPL: Lower limit breached, but inside cooldown
        mock_data_inst.get_stock_latest_trade.return_value = {"AAPL": trade_aapl}

        env = {
            "ALPACA_API_KEY": "fake_key",
            "ALPACA_SECRET_KEY": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "fake_chat_id",
            "BYPASS_MARKET_OPEN_CHECK": "True",
            "COOLDOWN_HOURS": "2.0",
            "ALPACA_FEED": "SIP"
        }

        with patch.dict(os.environ, env, clear=True):
            monitor.run_monitor()

        # Verify feed parameter was set to DataFeed.SIP
        call_arg = mock_data_inst.get_stock_latest_trade.call_args[0][0]
        self.assertEqual(call_arg.feed, DataFeed.SIP)

        # No alert sent due to cooldown
        mock_send.assert_not_called()
        self.assertEqual(rows[0]["Last_Price"], "165.00")
        # Last_Triggered timestamp should remain unchanged
        self.assertEqual(rows[0]["Last_Triggered"], thirty_mins_ago_str)
        mock_save.assert_called_once()

    @patch("monitor.TradingClient")
    @patch("monitor.StockHistoricalDataClient")
    @patch("monitor.load_config")
    @patch("monitor.save_config")
    @patch("monitor.send_telegram_msg")
    def test_run_monitor_outside_cooldown(self, mock_send, mock_save, mock_load, mock_hist, mock_trade):
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        three_hours_ago = now_utc - datetime.timedelta(hours=3)
        three_hours_ago_str = three_hours_ago.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = [
            {"Ticker": "AAPL", "Lower_Limit": "170.50", "Upper_Limit": "195.00", "Active": "TRUE", "Last_Triggered": three_hours_ago_str, "Last_Price": ""}
        ]
        mock_load.return_value = (rows, list(rows[0].keys()))

        mock_data_inst = MagicMock()
        mock_hist.return_value = mock_data_inst

        trade_aapl = MagicMock()
        trade_aapl.price = 165.0  # AAPL: Lower limit breached, outside 2-hour cooldown
        mock_data_inst.get_stock_latest_trade.return_value = {"AAPL": trade_aapl}

        env = {
            "ALPACA_API_KEY": "fake_key",
            "ALPACA_SECRET_KEY": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "fake_chat_id",
            "BYPASS_MARKET_OPEN_CHECK": "True",
            "COOLDOWN_HOURS": "2.0"
        }

        with patch.dict(os.environ, env, clear=True):
            monitor.run_monitor()

        mock_send.assert_called_once()
        self.assertEqual(rows[0]["Last_Price"], "165.00")
        self.assertNotEqual(rows[0]["Last_Triggered"], three_hours_ago_str)
        mock_save.assert_called_once()

    @patch("monitor.TradingClient")
    @patch("monitor.StockHistoricalDataClient")
    @patch("monitor.load_config")
    @patch("monitor.save_config")
    @patch("monitor.send_telegram_msg")
    def test_run_monitor_failed_ticker(self, mock_send, mock_save, mock_load, mock_hist, mock_trade):
        rows = [
            {"Ticker": "INVALID_TICKER", "Lower_Limit": "170.50", "Upper_Limit": "195.00", "Active": "TRUE", "Last_Triggered": "", "Last_Price": ""}
        ]
        mock_load.return_value = (rows, list(rows[0].keys()))

        mock_data_inst = MagicMock()
        mock_hist.return_value = mock_data_inst

        # Simulate Alpaca failing to fetch/find the stock
        mock_data_inst.get_stock_latest_trade.return_value = {}

        env = {
            "ALPACA_API_KEY": "fake_key",
            "ALPACA_SECRET_KEY": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "fake_chat_id",
            "BYPASS_MARKET_OPEN_CHECK": "True"
        }

        with patch.dict(os.environ, env, clear=True):
            monitor.run_monitor()

        # A warning telegram message should be sent
        mock_send.assert_called_once()
        msg_arg = mock_send.call_args[0][0]
        self.assertIn("INVALID_TICKER", msg_arg)
        self.assertIn("could not be fetched from Alpaca", msg_arg)

        # Save was not called because no price updates occurred
        mock_save.assert_not_called()

if __name__ == "__main__":
    unittest.main()
