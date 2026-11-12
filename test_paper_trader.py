import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paper_trader as pt


class PaperTraderTests(unittest.TestCase):
    def test_normalizes_collector_schema(self):
        pair = {
            "chain": "solana", "symbol": "BONK", "token_address": "bonk123",
            "quote_symbol": "USDC", "price_usd": 0.00002,
            "liquidity_usd": 50000, "volume_24h_usd": 80000,
            "price_change_1h_pct": 2, "price_change_24h_pct": 5,
        }
        result = pt.normalize(pair)
        self.assertEqual(result["address"], "bonk123")
        self.assertEqual(result["symbol"], "BONK")
        self.assertEqual(result["price"], 0.00002)

    def test_rejects_stablecoin_base_and_wrong_chain(self):
        self.assertIsNone(pt.normalize({
            "chain": "solana", "symbol": "USDC", "token_address": "usd",
            "quote_symbol": "SOL", "price_usd": 1,
        }))
        self.assertIsNone(pt.normalize({
            "chain": "ethereum", "symbol": "BONK", "token_address": "b",
            "quote_symbol": "USDC", "price_usd": 1,
        }))

    def test_reads_collector_snapshot_and_filters_momentum(self):
        snapshot = {
            "timestamp_utc": "2026-11-12T12:00:00+00:00",
            "candidates": [{
                "chain": "solana", "symbol": "BONK", "token_address": "bonk123",
                "quote_symbol": "USDC", "price_usd": 0.00002,
                "liquidity_usd": 50000, "volume_24h_usd": 80000,
                "price_change_1h_pct": 2, "price_change_24h_pct": 5,
            }]
        }
        with patch.object(pt, "MIN_LIQUIDITY_USD", 10000), patch.object(pt, "MIN_VOLUME_24H_USD", 10000):
            pairs, stamp = pt.eligible_candidates(snapshot)
        self.assertEqual(stamp, snapshot["timestamp_utc"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["symbol"], "BONK")

    def test_initial_state_uses_configured_virtual_cash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pt, "STATE", Path(tmp) / "paper_portfolio.json"), patch.object(pt, "STARTING_CASH", 100.0):
                state = pt.load_state()
        self.assertEqual(state["cash"], 100.0)
        self.assertEqual(state["mode"], "paper_only")
        self.assertEqual(state["trades"], [])

    def test_invalid_existing_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_portfolio.json"
            path.write_text('{"cash": 10}', encoding="utf-8")
            with patch.object(pt, "STATE", path):
                with self.assertRaises(ValueError):
                    pt.load_state()


    def test_simulation_buys_from_real_collector_schema_and_persists_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            snapshot = {
                "timestamp_utc": "2026-11-12T12:00:00+00:00",
                "candidates": [{
                    "chain": "solana", "symbol": "BONK", "token_address": "bonk123",
                    "quote_symbol": "USDC", "price_usd": 0.00002,
                    "liquidity_usd": 50000, "volume_24h_usd": 80000,
                    "price_change_1h_pct": 2, "price_change_24h_pct": 5,
                }]
            }
            (data / "market_snapshots.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            with patch.object(pt, "DATA", data), patch.object(pt, "SNAPSHOTS", data / "market_snapshots.jsonl"), \
                 patch.object(pt, "STATE", data / "paper_portfolio.json"), patch.object(pt, "LOG", data / "engine.log"), \
                 patch.object(pt, "STARTING_CASH", 100.0):
                summary = pt.run_simulation()
                state = json.loads((data / "paper_portfolio.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["trade_count"], 1)
            self.assertEqual(summary["open_positions"], 1)
            self.assertEqual(summary["starting_cash"], 100.0)
            self.assertEqual(state["trades"][0]["side"], "BUY")
            self.assertLess(summary["cash"], 100.0)


if __name__ == "__main__":
    unittest.main()
