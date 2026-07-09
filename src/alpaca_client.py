from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class AlpacaError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlpacaClient:
    api_key_id: str
    api_secret_key: str
    base_url: str
    data_url: str

    def _headers(self) -> dict[str, str]:
        if not self.api_key_id or not self.api_secret_key:
            raise AlpacaError("Missing Alpaca API credentials.")

        return {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(body).encode("utf-8")

        request = urllib.request.Request(
            url=url,
            data=encoded_body,
            headers=self._headers(),
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AlpacaError(f"Alpaca HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AlpacaError(f"Alpaca connection error: {exc.reason}") from exc

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}/v2/account")

    def get_positions(self) -> list[dict[str, Any]]:
        response = self._request("GET", f"{self.base_url}/v2/positions")
        return response if isinstance(response, list) else []

    def get_orders(self, *, status: str = "open") -> list[dict[str, Any]]:
        response = self._request("GET", f"{self.base_url}/v2/orders", params={"status": status})
        return response if isinstance(response, list) else []

    def get_clock(self) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}/v2/clock")

    def get_asset(self, symbol: str) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}/v2/assets/{symbol}")

    def get_assets(
        self,
        *,
        status: str = "active",
        asset_class: str = "us_equity",
    ) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            f"{self.base_url}/v2/assets",
            params={"status": status, "asset_class": asset_class},
        )
        return response if isinstance(response, list) else []

    def get_stock_bars(
        self,
        symbol: str,
        *,
        timeframe: str,
        limit: int,
        start: str | None = None,
        end: str | None = None,
        feed: str = "iex",
    ) -> list[dict[str, Any]]:
        params = {"timeframe": timeframe, "limit": limit, "feed": feed}
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        response = self._request(
            "GET",
            f"{self.data_url}/v2/stocks/{symbol}/bars",
            params=params,
        )
        bars = response.get("bars", [])
        return bars if isinstance(bars, list) else []

    def get_latest_quote(self, symbol: str, *, feed: str = "iex") -> dict[str, Any]:
        response = self._request(
            "GET",
            f"{self.data_url}/v2/stocks/{symbol}/quotes/latest",
            params={"feed": feed},
        )
        quote = response.get("quote", {})
        return quote if isinstance(quote, dict) else {}

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"{self.base_url}/v2/orders", body=order)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.base_url}/v2/orders/{order_id}")

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"{self.base_url}/v2/orders/{order_id}")

    def close_position(self, symbol: str) -> dict[str, Any]:
        return self._request("DELETE", f"{self.base_url}/v2/positions/{symbol}")
