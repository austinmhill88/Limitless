import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Set
import logging

from bot.config.settings import settings

logger = logging.getLogger("limitless.earnings")
TZ_ET = ZoneInfo("America/New_York")

class EarningsCalendar:
    def __init__(self):
        self.skip_dates: Dict[str, Set[str]] = {}  # symbol -> set of ISO dates to skip

    def refresh_symbol(self, symbol: str):
        # Finnhub endpoint: /calendar/earnings?symbol=TSLA
        # Note: You may need to handle rate limits; cache results daily
        if not settings.finnhub_api_key:
            logger.warning("Finnhub API key not configured, skipping earnings calendar refresh for %s", symbol)
            return
        
        url = "https://finnhub.io/api/v1/calendar/earnings"
        params = {"symbol": symbol, "token": settings.finnhub_api_key}
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.Timeout:
            logger.error("Timeout fetching earnings calendar for %s", symbol)
            return
        except requests.exceptions.RequestException as e:
            logger.error("Error fetching earnings calendar for %s: %s", symbol, e)
            return
        except ValueError as e:
            logger.error("Invalid JSON response for earnings calendar for %s: %s", symbol, e)
            return

        days = set()
        for item in data.get("earningsCalendar", []):
            date = item.get("date")
            if not date:
                continue
            days.add(date)
            if settings.earnings_skip_next_day:
                try:
                    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TZ_ET)
                    next_day = d + timedelta(days=1)
                    days.add(next_day.strftime("%Y-%m-%d"))
                except Exception:
                    pass

        self.skip_dates[symbol] = days

    def is_skip_day(self, symbol: str, today_iso: str) -> bool:
        return today_iso in self.skip_dates.get(symbol, set())

earnings = EarningsCalendar()