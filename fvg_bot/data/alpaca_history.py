from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fvg_bot.data.store import SessionDay, parse_rfc3339

DATA_URL = "https://data.alpaca.markets"
PAPER_URL = "https://paper-api.alpaca.markets"
MAX_PAGE = 10_000
RETRYABLE = {429, 500, 502, 503, 504}

HttpGet = Callable[[str, Mapping[str, str]], Tuple[int, bytes]]


class AlpacaHTTPError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Alpaca HTTP {status}: {detail}")
        self.status = status


def urllib_get(url: str, headers: Mapping[str, str]) -> Tuple[int, bytes]:
    try:
        with urlopen(Request(url, headers=dict(headers)), timeout=30) as r:
            return r.status, r.read()
    except HTTPError as e:
        return e.code, e.read()


def _utc(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AlpacaHistory:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        http_get: HttpGet = urllib_get,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        min_interval: float = 0.35,  # about 170 requests/min, under the free plan's 200/min
        max_retries: int = 5,
    ):
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self._http_get = http_get
        self._sleep = sleep
        self._monotonic = monotonic
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._last_request: Optional[float] = None

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._monotonic()

    def _get(self, base: str, path: str, params: Mapping[str, str]) -> dict:
        url = f"{base}{path}?{urlencode(params)}"
        for attempt in range(self._max_retries + 1):
            self._throttle()
            status, body = self._http_get(url, self._headers)
            if status == 200:
                return json.loads(body)
            if status not in RETRYABLE or attempt == self._max_retries:
                raise AlpacaHTTPError(status, body[:300].decode("utf-8", "replace"))
            self._sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def stock_bars(
        self, symbol: str, start: datetime, end: datetime, feed: str, timeframe: str = "1Min"
    ) -> List[dict]:
        """All bars in [start, end], extended hours included, raw (unadjusted) prices."""
        params: Dict[str, str] = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": _utc(start),
            "end": _utc(end),
            "limit": str(MAX_PAGE),
            "adjustment": "raw",
            "feed": feed,
            "sort": "asc",
        }
        rows: List[dict] = []
        while True:
            body = self._get(DATA_URL, "/v2/stocks/bars", params)
            rows.extend((body.get("bars") or {}).get(symbol, []))
            token = body.get("next_page_token")
            if not token:
                return rows
            params = {**params, "page_token": token}

    def calendar(self, start: date, end: date, market: str = "NYSE") -> List[SessionDay]:
        body = self._get(
            PAPER_URL, f"/v3/calendar/{market}", {"start": start.isoformat(), "end": end.isoformat()}
        )
        return [
            SessionDay(
                day=date.fromisoformat(d["date"]),
                open=parse_rfc3339(d["core_start"]),
                close=parse_rfc3339(d["core_end"]),
            )
            for d in body["calendar"]
        ]
