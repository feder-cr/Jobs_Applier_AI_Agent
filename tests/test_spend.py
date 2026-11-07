"""What the model calls cost: tokens at the gateway's own prices, per call, per application, and said in plain words."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from aihawk.llm import Spend, money
from aihawk.store import Store


def usage(prompt: int, completion: int, cached: int = 0) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=SimpleNamespace(cached_tokens=cached))


def test_a_call_costs_its_tokens_at_the_models_prices_and_cached_tokens_at_theirs() -> None:
    spend = Spend()
    spend.prices["m"] = (0.15 / 1_000_000, 0.60 / 1_000_000, 0.003 / 1_000_000)
    cost = spend.note("m", usage(17_441, 13))
    assert round(cost, 6) == round(17_441 * 0.15e-6 + 13 * 0.60e-6, 6)
    cost = spend.note("m", usage(1_000_000, 0, cached=1_000_000))
    assert round(cost, 6) == 0.003
    assert round(spend.total, 6) == round(17_441 * 0.15e-6 + 13 * 0.60e-6 + 0.003, 6)


def test_a_model_the_list_does_not_price_is_counted_as_unpriced_and_a_missing_usage_costs_nothing() -> None:
    spend = Spend()
    assert spend.note("unknown", usage(10, 10)) == 0.0 and spend.unpriced == 1
    assert spend.note("unknown", None) == 0.0 and spend.unpriced == 1 and spend.total == 0.0


def test_money_says_a_cost_as_a_person_would() -> None:
    assert money(0.0001) == "under a cent"
    assert money(0.0431) == "4 cents"
    assert money(1.2) == "1.20 $"


def test_a_store_from_before_costs_were_kept_gets_the_column_and_an_application_keeps_its_cost(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL, url TEXT NOT NULL,"
        " apply_url TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', score REAL, report TEXT, ref TEXT,"
        " lexical REAL NOT NULL DEFAULT 0, evidence REAL, note TEXT, sent_at TEXT, final_url TEXT, verdict TEXT, verdict_evidence TEXT,"
        " page_text TEXT, screenshot TEXT, first_seen TEXT NOT NULL, updated TEXT NOT NULL)"
    )
    old.execute(
        "INSERT INTO jobs (id, company, title, location, url, apply_url, description, status, first_seen, updated)"
        " VALUES ('a', 'Co', 'Eng', 'Rome', 'u', 'u', 'd', 'submitted', 't', 't')"
    )
    old.commit()
    old.close()
    store = Store(path)
    assert store.sent()[0].cost is None
    store.set_cost("a", 0.0312)
    assert store.sent()[0].cost == 0.0312
    store.close()


def test_settling_replaces_each_estimate_with_what_the_gateway_charged_and_keeps_the_estimate_it_cannot_find() -> None:
    import asyncio

    spend = Spend()
    spend.prices["m"] = (0.15 / 1_000_000, 0.60 / 1_000_000, 0.003 / 1_000_000)
    spend.note("m", usage(10_000, 0), request_id="known")
    spend.note("m", usage(10_000, 0), request_id="lost")
    spend.note("other", usage(10_000, 0), request_id="priced-only-by-the-gateway")
    assert round(spend.total, 6) == 0.003 and spend.unpriced == 1

    class Gateway:
        async def get(self, path: str, cast_to: type) -> dict:
            if "id=known" in path:
                return {"data": {"total_cost": 0.003}}
            if "id=priced-only" in path:
                return {"data": {"total_cost": 0.002}}
            raise RuntimeError("no generation found")

    asyncio.run(spend.settle(Gateway()))  # type: ignore[arg-type]
    assert round(spend.total, 6) == round(0.003 + 0.0015 + 0.002, 6) and spend.unpriced == 0 and spend.pending == []
