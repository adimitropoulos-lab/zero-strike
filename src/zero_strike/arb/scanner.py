"""Mispricing scanner.

Polymarket markets price each outcome on [0, 1]. Mutually-exclusive outcomes
*should* sum to 1.0 minus fees. When the sum of best-ask prices is < 1, there's
a risk-free arb: buy one share of every outcome for less than $1 and collect $1
when the market resolves.

Conversely if the sum of best-bid prices is > 1, you can sell one share of every
outcome and collect more than $1, again risk-free.

We surface both as `ArbOpportunity` with the implied profit per dollar locked.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..polymarket import ClobClient, GammaClient


@dataclass
class ArbOpportunity:
    market_id: str
    question: str
    direction: str  # "buy_all" | "sell_all"
    outcomes: list[str]
    prices: list[float]
    sum_price: float
    profit_per_dollar: float
    volume_24h: float
    end_date: str | None

    def as_row(self) -> dict:
        return {
            "market_id": self.market_id,
            "direction": self.direction,
            "sum_price": round(self.sum_price, 4),
            "profit_per_dollar": round(self.profit_per_dollar, 4),
            "volume_24h": self.volume_24h,
            "end_date": self.end_date,
            "question": self.question,
        }


def _check_market(market: dict, clob: ClobClient, *, fee_bps: int) -> ArbOpportunity | None:
    token_ids = GammaClient.outcome_token_ids(market)
    labels = GammaClient.outcome_labels(market)
    if len(token_ids) < 2:
        return None
    quotes = clob.prices_for_outcomes(token_ids)
    asks = [quotes[t]["ask"] for t in token_ids]
    bids = [quotes[t]["bid"] for t in token_ids]
    if any(a is None for a in asks) or any(b is None for b in bids):
        return None

    fee = fee_bps / 10_000
    sum_ask = sum(asks)  # cost to buy a complete set
    sum_bid = sum(bids)  # proceeds from selling a complete set
    end_date = market.get("endDate") or market.get("end_date_iso")
    vol = float(market.get("volume24hr") or market.get("volumeNum") or 0)

    if sum_ask < 1 - fee:
        return ArbOpportunity(
            market_id=str(market.get("id") or market.get("conditionId") or ""),
            question=str(market.get("question", "")),
            direction="buy_all",
            outcomes=labels or token_ids,
            prices=asks,
            sum_price=sum_ask,
            profit_per_dollar=(1 - fee) / sum_ask - 1,
            volume_24h=vol,
            end_date=end_date,
        )
    if sum_bid > 1 + fee:
        return ArbOpportunity(
            market_id=str(market.get("id") or market.get("conditionId") or ""),
            question=str(market.get("question", "")),
            direction="sell_all",
            outcomes=labels or token_ids,
            prices=bids,
            sum_price=sum_bid,
            profit_per_dollar=sum_bid / (1 + fee) - 1,
            volume_24h=vol,
            end_date=end_date,
        )
    return None


def scan_arbitrage(
    *,
    max_markets: int = 500,
    min_volume_24h: float = 1_000,
    fee_bps: int = 200,
) -> list[ArbOpportunity]:
    """Scan active markets for outcome-price arb (set ≠ 1)."""
    found: list[ArbOpportunity] = []
    with GammaClient() as gamma, ClobClient() as clob:
        seen = 0
        for market in gamma.iter_markets(active=True, closed=False, order="volume24hr"):
            if seen >= max_markets:
                break
            seen += 1
            vol = float(market.get("volume24hr") or market.get("volumeNum") or 0)
            if vol < min_volume_24h:
                continue
            opp = _check_market(market, clob, fee_bps=fee_bps)
            if opp is not None:
                found.append(opp)
    found.sort(key=lambda o: o.profit_per_dollar, reverse=True)
    return found
