"""What a session would cost at list prices. THE ONLY place a price lives.

WHAT THIS NUMBER IS, said precisely, because the wrong reading of it is worse than no
number: it is what the tokens in a session would cost **on the Anthropic API at list
prices**. Most people running Claude Code are on a subscription and are not billed per
token, so this is not a bill. It is the size of the work, in the one unit everybody
already has an instinct for, and it is the only honest way to compare an Opus session
against a Haiku one.

The label travels with the number everywhere (`BASIS_LIST_PRICE`) and the phone prints it.
"About $12 of API usage" is true and useful. "You spent $12" is false for a subscriber and
is exactly the plausible wrong number this codebase exists to refuse.

PRICES ARE A MEASUREMENT AND THEY GO STALE. Every row carries the day it was read and
where from. A price nobody has checked in a year is still a number the product will
happily multiply, so `PRICES_READ_ON` is compared against the clock and the metric is
labelled `stale_prices` past `PRICE_STALE_DAYS` rather than silently drifting.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

#: The day the table below was read, and from where. Bump BOTH when you touch a price.
PRICES_READ_ON = dt.date(2026, 9, 6)
PRICES_SOURCE = "Anthropic list prices, docs.anthropic.com/en/docs/about-claude/pricing"

#: Past this, the table is old enough that the product should say so rather than pretend.
#: 180 days: two model generations have shipped inside that window more than once.
PRICE_STALE_DAYS = 180

#: What the number means. Never a bill.
BASIS_LIST_PRICE = "anthropic_api_list_price"
#: The token counts were not reported by the harness (Cursor writes {0, 0} on all 14,565
#: of its message rows). Absent, not zero.
BASIS_TOKENS_ABSENT = "tokens_not_reported"
#: The model id is not one this table knows. Refused rather than priced at a guess.
BASIS_UNKNOWN_MODEL = "model_not_in_price_table"


@dataclasses.dataclass(frozen=True)
class Price:
    """Dollars per MILLION tokens."""

    input: float
    output: float
    #: Cache reads cost 0.1x input on every model except Claude Fable 5.1, which is
    #: 0.025x ($0.25/MTok). Stated per model rather than as a multiplier for that reason.
    cache_read: float

    @property
    def cache_write_5m(self) -> float:
        """1.25x input, the 5-minute TTL write premium."""
        return self.input * 1.25

    @property
    def cache_write_1h(self) -> float:
        """2x input, the 1-hour TTL write premium. The doubled write is why the 1-hour
        TTL needs three requests to pay for itself where 5 minutes needs two."""
        return self.input * 2.0


#: Dollars per million tokens, read on PRICES_READ_ON. Keys are the model ids the
#: harnesses actually write, lowercased. A `[1m]` context suffix is stripped before
#: lookup (the wire preserves it verbatim; the price does not depend on it).
PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, 0.25),
    "claude-mythos-5-1": Price(10.0, 50.0, 0.25),
    "claude-fable-5": Price(10.0, 50.0, 1.0),
    "claude-opus-5": Price(5.0, 25.0, 0.5),
    "claude-opus-4-8": Price(5.0, 25.0, 0.5),
    "claude-opus-4-7": Price(5.0, 25.0, 0.5),
    "claude-opus-4-6": Price(5.0, 25.0, 0.5),
    "claude-sonnet-5": Price(2.0, 10.0, 0.2),
    "claude-sonnet-4-6": Price(3.0, 15.0, 0.3),
    "claude-haiku-4-5": Price(1.0, 5.0, 0.1),
}

#: Short names a person would say out loud, for the one line on a card. Longest prefix
#: wins, so `claude-opus-4-8` reads "Opus 4.8" and `claude-opus-5` reads "Opus 5".
FAMILIES: tuple[tuple[str, str], ...] = (
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-mythos-5-1", "Mythos 5.1"),
    ("claude-fable-5", "Fable 5"),
    ("claude-opus-5", "Opus 5"),
    ("claude-opus-4-8", "Opus 4.8"),
    ("claude-opus-4-7", "Opus 4.7"),
    ("claude-opus-4-6", "Opus 4.6"),
    ("claude-sonnet-5", "Sonnet 5"),
    ("claude-sonnet-4-6", "Sonnet 4.6"),
    ("claude-haiku-4-5", "Haiku 4.5"),
)


@dataclasses.dataclass(frozen=True)
class Tokens:
    """The five buckets, exactly as `privacy/upload-contract.json` carries them.

    There is no `total`, on purpose: `cache_read` IS billed, at a tenth of the input rate,
    so adding the buckets up and multiplying by one price overcharges cache-heavy work by
    a factor that grows with how well the cache is working.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_w5m: int = 0
    cache_w1h: int = 0

    @property
    def any(self) -> bool:
        return bool(self.input or self.output or self.cache_read or self.cache_w5m or self.cache_w1h)


def normalize(model_id: str) -> str:
    """The lookup key: lowercased, with a `[1m]` context suffix stripped.

    The suffix is preserved verbatim on the wire because it says which context window was
    in play, and it does not change the price.
    """
    key = (model_id or "").strip().lower()
    for suffix in ("[1m]", "[1m", " (1m)"):
        if key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    return key


def family(model_id: str) -> str:
    """"Opus 5", "Sonnet 5", or the raw id when the table has never heard of it."""
    key = normalize(model_id)
    for prefix, name in FAMILIES:
        if key.startswith(prefix):
            return name
    return model_id


def price_for(model_id: str) -> Price | None:
    """The row for a model, or None. None is a refusal, never a default rate.

    Pricing an unknown model at "whatever Sonnet costs" would produce a confident dollar
    figure for a model nobody has ever checked the price of, which is the exact shape of
    the bug this repo is written to avoid.
    """
    return PRICES.get(normalize(model_id))


def cost_usd(tokens: Tokens, model_id: str) -> float | None:
    """List-price cost of these tokens on this model, or None if the model is unknown."""
    p = price_for(model_id)
    if p is None:
        return None
    return (
        tokens.input * p.input
        + tokens.output * p.output
        + tokens.cache_read * p.cache_read
        + tokens.cache_w5m * p.cache_write_5m
        + tokens.cache_w1h * p.cache_write_1h
    ) / 1_000_000


def split_by_model(tokens: Tokens, output_share: dict[str, float]) -> dict[str, Tokens]:
    """Split one session's buckets across the models that worked in it.

    THE ASSUMPTION, stated because it is one: only the OUTPUT split is measured. The wire
    carries `output_token_share` per model and nothing per model for input or cache
    (privacy/upload-contract.json), so input and cache are attributed in the same
    proportion as output.

    Why that is defensible and where it is not: within one session the models are usually
    reading the same conversation, so the input each sees is close to proportional to how
    much of the turn-taking it did. It is wrong when a cheap model is used for a lot of
    small reads and an expensive one for a few large generations. That case makes this an
    ESTIMATE, and the label says so; a session with one model, which is most of them, is
    exact.
    """
    if not output_share:
        return {}
    total = sum(output_share.values())
    if total <= 0:
        return {}
    out: dict[str, Tokens] = {}
    for model, share in output_share.items():
        f = share / total
        out[model] = Tokens(
            input=round(tokens.input * f),
            output=round(tokens.output * f),
            cache_read=round(tokens.cache_read * f),
            cache_w5m=round(tokens.cache_w5m * f),
            cache_w1h=round(tokens.cache_w1h * f),
        )
    return out


def session_cost(tokens: Tokens, output_share: dict[str, float]) -> tuple[float | None, str]:
    """(dollars, basis) for one session. None means refused, and the basis says why."""
    if not tokens.any:
        return None, BASIS_TOKENS_ABSENT
    if not output_share:
        return None, BASIS_UNKNOWN_MODEL
    total = 0.0
    for model, part in split_by_model(tokens, output_share).items():
        one = cost_usd(part, model)
        if one is None:
            # One unknown model poisons the session total: the rest of it would be a real
            # number missing an unknown amount, which reads as complete and is not.
            return None, BASIS_UNKNOWN_MODEL
        total += one
    return total, BASIS_LIST_PRICE


def prices_are_stale(today: dt.date | None = None) -> bool:
    return ((today or dt.date.today()) - PRICES_READ_ON).days > PRICE_STALE_DAYS


def money(usd: float) -> str:
    """Dollars the way a person says them: "$0.42", "$12.30", "$1,204"."""
    if usd < 1:
        return f"${usd:.2f}"
    if usd < 100:
        return f"${usd:,.2f}"
    return f"${round(usd):,}"



def priced_session(ledger, dominant_share: float):
    """(cost in dollars, the model that wrote most of it) for one sitting, or (None, None).

    Shared by `python -m analysis narrative` and `python -m capture narrative` so the two
    commands cannot put different dollar figures on one corpus. The price table and the
    refusal rules live in `analysis/pricing.py`; this is only the plumbing from a ledger.
    """
    from analysis import pricing

    if not ledger.reported or not ledger.buckets or not ledger.models:
        return None, None
    share = {m["model_id"]: float(m["output_token_share"]) for m in ledger.models}
    usd, _basis = pricing.session_cost(pricing.Tokens(**ledger.buckets), share)
    top = max(share, key=share.get) if share else None
    dominant = top if top and share[top] >= dominant_share else None
    return usd, dominant


__all__ = [
    "BASIS_LIST_PRICE",
    "BASIS_TOKENS_ABSENT",
    "BASIS_UNKNOWN_MODEL",
    "FAMILIES",
    "PRICES",
    "PRICES_READ_ON",
    "PRICES_SOURCE",
    "PRICE_STALE_DAYS",
    "Price",
    "Tokens",
    "cost_usd",
    "family",
    "money",
    "normalize",
    "price_for",
    "priced_session",
    "prices_are_stale",
    "session_cost",
    "split_by_model",
]
