import unittest

from exit_rules import ExitConfig, evaluate_tick, estimated_break_even_price, estimated_net_pnl


class ExitRuleTests(unittest.TestCase):
    def setUp(self):
        self.position = {
            "symbol": "TEST", "entry_price": 10.0,
            "quantity": 9.95, "cost_basis": 100.0,
            "last_price": 10.0,
        }
        self.config = ExitConfig()

    def test_net_profit_target_accounts_for_both_exit_costs(self):
        # A price of 10.20 is not enough to guarantee +1% net after fees/slippage.
        result = evaluate_tick(self.position, 10.20, self.config)
        self.assertIsNone(result["exit_reason"])
        target_price = 101.0 / (self.position["quantity"] * (1-self.config.slippage) * (1-self.config.fee_rate))
        result = evaluate_tick(self.position, target_price, self.config)
        self.assertEqual(result["exit_reason"], "take_profit_net")
        self.assertAlmostEqual(result["estimated_net_return"], 0.01, places=8)

    def test_hard_stop_triggers_at_three_percent_below_entry(self):
        result = evaluate_tick(self.position, 9.70, self.config)
        self.assertEqual(result["exit_reason"], "stop_loss")

    def test_trailing_stop_arms_after_net_break_even_and_protects_break_even_floor(self):
        be = estimated_break_even_price(self.position, self.config)
        first = evaluate_tick(self.position, be * 1.02, self.config)
        self.assertTrue(first["position"]["trailing_armed"])
        self.assertGreaterEqual(first["trailing_stop_price"], be)
        second = evaluate_tick(first["position"], first["position"]["highest_price"] * 0.985, self.config)
        self.assertEqual(second["exit_reason"], "trailing_stop")

    def test_high_water_mark_never_moves_down(self):
        first = evaluate_tick(self.position, 10.8, self.config)
        second = evaluate_tick(first["position"], 10.3, self.config)
        self.assertEqual(second["position"]["highest_price"], 10.8)

    def test_net_pnl_subtracts_exit_fee_and_slippage(self):
        pnl = estimated_net_pnl(self.position, 10.0, self.config)
        self.assertLess(pnl, -1.0)

    def test_input_position_is_not_mutated(self):
        original = dict(self.position)
        evaluate_tick(self.position, 10.5, self.config)
        self.assertEqual(self.position, original)

    def test_invalid_price_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_tick(self.position, 0, self.config)


if __name__ == "__main__":
    unittest.main()
