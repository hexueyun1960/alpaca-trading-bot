from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.strategy import Signal, calculate_spread_pct, calculate_vwap_deviation_pct, position_direction


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_timestamp(value: object) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def build_client_order_id(strategy: str, symbol: str, side: str, timestamp: datetime, attempt: int = 1) -> str:
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{strategy}-{symbol.upper()}-{side.upper()}-{stamp}-{attempt:02d}"


def marketable_limit_price(side: str, bid: float, ask: float, max_slippage_pct: float) -> float:
    slippage = max(max_slippage_pct, 0.0) / 100
    if side == "sell":
        return round(bid * (1 - slippage), 2)
    return round(ask * (1 + slippage), 2)


def fixed_notional_qty(notional: float, price: float) -> float:
    if notional <= 0 or price <= 0:
        return 0.0
    return float(int(notional / price))


@dataclass
class RealtimeBar:
    timestamp: datetime
    open: float
    close: float
    vwap: float
    volume: float


@dataclass
class QuoteState:
    bid: float = 0.0
    ask: float = 0.0
    timestamp: datetime | None = None

    def age_seconds(self, now: datetime) -> float:
        if self.timestamp is None:
            return float("inf")
        return max((now - self.timestamp).total_seconds(), 0.0)


@dataclass
class SymbolRealtimeState:
    symbol: str
    bars: deque[RealtimeBar] = field(default_factory=lambda: deque(maxlen=60))
    quote: QuoteState = field(default_factory=QuoteState)
    session_open: float | None = None
    candidate_until: datetime | None = None
    candidate_rank: int | None = None
    candidate_reason: str | None = None
    trigger_locked: bool = False
    pending_client_order_id: str | None = None

    def add_bar(self, message: dict) -> None:
        open_price = _as_float(message.get("o", message.get("open")))
        if self.session_open is None and open_price > 0:
            self.session_open = open_price
        self.bars.append(
            RealtimeBar(
                timestamp=_parse_timestamp(message.get("t")),
                open=open_price,
                close=_as_float(message.get("c")),
                vwap=_as_float(message.get("vw")),
                volume=_as_float(message.get("v")),
            ),
        )

    def update_quote(self, message: dict) -> None:
        self.quote = QuoteState(
            bid=_as_float(message.get("bp")),
            ask=_as_float(message.get("ap")),
            timestamp=_parse_timestamp(message.get("t")),
        )

    @property
    def latest_bar(self) -> RealtimeBar | None:
        return self.bars[-1] if self.bars else None

    def gain_pct(self, minutes: int) -> float | None:
        if len(self.bars) < minutes + 1:
            return None
        current = self.bars[-1].close
        previous = self.bars[-(minutes + 1)].close
        if previous <= 0:
            return None
        return ((current - previous) / previous) * 100

    def relative_volume(self, lookback: int = 20) -> float | None:
        if len(self.bars) < 2:
            return None
        current = self.bars[-1].volume
        available_lookback = min(lookback, len(self.bars) - 1)
        history = [bar.volume for bar in list(self.bars)[-(available_lookback + 1) : -1] if bar.volume > 0]
        if not history:
            return None
        return current / (sum(history) / len(history))

    def intraday_dollar_volume(self) -> float:
        return sum(bar.close * bar.volume for bar in self.bars if bar.close > 0 and bar.volume > 0)

    def day_change_pct(self) -> float | None:
        latest = self.latest_bar
        if latest is None or latest.close <= 0 or not self.session_open or self.session_open <= 0:
            return None
        return ((latest.close - self.session_open) / self.session_open) * 100

    def is_candidate(self, now: datetime) -> bool:
        return self.candidate_until is not None and self.candidate_until > now


@dataclass(frozen=True)
class RealtimeDecision:
    signal: Signal | None
    reason: str


class RealtimeSignalEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.symbols: dict[str, SymbolRealtimeState] = {}
        self.websocket_connected = False

    def state_for(self, symbol: str) -> SymbolRealtimeState:
        symbol = symbol.upper()
        state = self.symbols.get(symbol)
        if state is None:
            state = SymbolRealtimeState(symbol=symbol)
            self.symbols[symbol] = state
        return state

    def set_session_open(self, symbol: str, session_open: float) -> None:
        state = self.state_for(symbol)
        if session_open > 0:
            state.session_open = session_open

    def set_session_opens(self, session_opens: dict[str, float]) -> None:
        for symbol, session_open in session_opens.items():
            self.set_session_open(symbol, session_open)

    def set_previous_close(self, symbol: str, previous_close: float) -> None:
        self.set_session_open(symbol, previous_close)

    def set_previous_closes(self, previous_closes: dict[str, float]) -> None:
        self.set_session_opens(previous_closes)

    def active_candidates(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        candidates = [state.symbol for state in self.symbols.values() if state.is_candidate(current)]
        candidates.sort()
        return candidates[: self.settings.max_candidate_symbols]

    def high_priority_symbols(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        ranked = []
        for state in self.symbols.values():
            if not state.is_candidate(current):
                continue
            day_change = state.day_change_pct() or 0
            ranked.append((state.symbol, abs(day_change)))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return [symbol for symbol, _change in ranked[: self.settings.max_high_priority_symbols]]

    def refresh_top_mover_candidates(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        ranked = []
        for state in self.symbols.values():
            day_change = state.day_change_pct()
            if day_change is None:
                continue
            ranked.append((state.symbol, day_change))

        gainers = sorted(ranked, key=lambda item: (-item[1], item[0]))[: self.settings.top_gainers_count]
        losers = sorted(ranked, key=lambda item: (item[1], item[0]))[: self.settings.top_losers_count]
        selected: dict[str, tuple[str, int]] = {}
        for rank, (symbol, _change) in enumerate(gainers, start=1):
            selected[symbol] = ("top_gainer", rank)
        for rank, (symbol, _change) in enumerate(losers, start=1):
            selected.setdefault(symbol, ("top_loser", rank))

        retention = max(self.settings.candidate_retention_seconds, self.settings.candidate_ttl_seconds, 0)
        candidate_until = current + timedelta(seconds=retention)
        for symbol, (reason, rank) in selected.items():
            state = self.state_for(symbol)
            state.candidate_until = candidate_until
            state.candidate_reason = reason
            state.candidate_rank = rank
        return sorted(selected)

    def retain_symbols(self, symbols: list[str], now: datetime | None = None, reason: str = "forced_retention") -> None:
        current = now or datetime.now(timezone.utc)
        retention = max(self.settings.candidate_retention_seconds, self.settings.candidate_ttl_seconds, 0)
        for symbol in symbols:
            state = self.state_for(symbol)
            state.candidate_until = current + timedelta(seconds=retention)
            state.candidate_reason = reason

    def on_bar(self, message: dict) -> list[str]:
        symbol = str(message.get("S", "")).upper()
        if not symbol:
            return []
        state = self.state_for(symbol)
        state.add_bar(message)

        return self.refresh_top_mover_candidates(state.latest_bar.timestamp)

    def on_quote(self, message: dict) -> None:
        symbol = str(message.get("S", "")).upper()
        if symbol:
            self.state_for(symbol).update_quote(message)

    def build_entry_signal(
        self,
        symbol: str,
        *,
        account_equity: float,
        buying_power: float | None = None,
        now: datetime | None = None,
    ) -> RealtimeDecision:
        current = now or datetime.now(timezone.utc)
        state = self.state_for(symbol)
        if not self.websocket_connected:
            return RealtimeDecision(None, "websocket disconnected")
        if state.trigger_locked or state.pending_client_order_id:
            return RealtimeDecision(None, "symbol trigger locked or order pending")
        if not state.is_candidate(current):
            return RealtimeDecision(None, "symbol is not in active candidate pool")
        latest = state.latest_bar
        if latest is None or latest.close <= 0 or latest.vwap <= 0:
            return RealtimeDecision(None, "latest bar missing price or VWAP")
        if state.quote.age_seconds(current) > self.settings.stale_quote_seconds:
            return RealtimeDecision(None, "quote is stale")

        bid = state.quote.bid
        ask = state.quote.ask
        spread_pct = calculate_spread_pct(bid, ask)
        if spread_pct is None or spread_pct > self.settings.max_spread_pct:
            return RealtimeDecision(None, "spread is too wide")

        deviation_pct = calculate_vwap_deviation_pct(latest.close, latest.vwap)
        if deviation_pct >= self.settings.vwap_entry_deviation_pct:
            side = "sell"
            intent = "sell_to_open"
            direction = "short"
            reason = f"realtime short: price {latest.close:.2f} is {deviation_pct:.2f}% above VWAP {latest.vwap:.2f}"
        elif deviation_pct <= -self.settings.vwap_entry_deviation_pct:
            side = "buy"
            intent = "buy_to_open"
            direction = "long"
            reason = f"realtime long: price {latest.close:.2f} is {deviation_pct:.2f}% below VWAP {latest.vwap:.2f}"
        else:
            return RealtimeDecision(None, "VWAP deviation below entry threshold")

        qty = fixed_notional_qty(self.settings.fixed_entry_notional, latest.close)
        if qty <= 0:
            return RealtimeDecision(None, "fixed notional is too small for one whole share")
        signal_time = latest.timestamp if latest.timestamp > current - timedelta(minutes=5) else current
        client_order_id = build_client_order_id("MR", symbol, side, signal_time)
        return RealtimeDecision(
            Signal(
                symbol=symbol.upper(),
                side=side,
                reason=reason,
                notional=round(qty * latest.close, 2),
                qty=qty,
                position_intent=intent,
                order_type="market",
                client_order_id=client_order_id,
                position_direction=direction,
                price=latest.close,
                vwap=latest.vwap,
                vwap_deviation_pct=deviation_pct,
            ),
            "approved",
        )

    def build_short_signal(
        self,
        symbol: str,
        *,
        account_equity: float,
        buying_power: float | None = None,
        now: datetime | None = None,
    ) -> RealtimeDecision:
        return self.build_entry_signal(
            symbol,
            account_equity=account_equity,
            buying_power=buying_power,
            now=now,
        )

    def build_exit_signal(self, symbol: str, position: dict, now: datetime | None = None) -> RealtimeDecision:
        current = now or datetime.now(timezone.utc)
        state = self.state_for(symbol)
        latest = state.latest_bar
        if latest is None or latest.close <= 0 or latest.vwap <= 0:
            return RealtimeDecision(None, "latest bar missing price or VWAP")
        direction = position_direction(position)
        qty = abs(_as_float(position.get("qty"), 0.0))
        if direction is None or qty <= 0:
            return RealtimeDecision(None, "no position to exit")
        deviation_pct = calculate_vwap_deviation_pct(latest.close, latest.vwap)
        if direction == "short" and latest.close > latest.vwap:
            return RealtimeDecision(None, "short has not crossed VWAP")
        if direction == "long" and latest.close < latest.vwap:
            return RealtimeDecision(None, "long has not crossed VWAP")
        side = "buy" if direction == "short" else "sell"
        intent = "buy_to_close" if direction == "short" else "sell_to_close"
        client_order_id = build_client_order_id("MRX", symbol, side, current)
        return RealtimeDecision(
            Signal(
                symbol=symbol.upper(),
                side=side,
                reason=f"realtime {direction} exit: price {latest.close:.2f} crossed VWAP {latest.vwap:.2f}",
                qty=qty,
                position_intent=intent,
                order_type="market",
                client_order_id=client_order_id,
                position_direction=direction,
                price=latest.close,
                vwap=latest.vwap,
                vwap_deviation_pct=deviation_pct,
            ),
            "approved",
        )

    def lock_order(self, signal: Signal) -> None:
        state = self.state_for(signal.symbol)
        state.trigger_locked = True
        state.pending_client_order_id = signal.client_order_id

    def release_order(self, symbol: str) -> None:
        state = self.state_for(symbol)
        state.pending_client_order_id = None
