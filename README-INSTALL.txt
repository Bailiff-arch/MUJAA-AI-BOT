MUJAA PAPER-TRADING INTEGRATION PATCH
=====================================

What this patch does
- Adapts paper_trader.py to the actual compact snapshot schema emitted by mujaa_bot.py.
- Excludes stablecoin/SOL base assets and only simulates pairs quoted in SOL, USDC or USDT.
- Uses a $100 virtual starting balance, configurable fees/slippage, a 3-position cap, 20% equity sizing, 5% stop loss and 10% take profit.
- Persists data/paper_portfolio.json on a separate `mujaa-paper-state` branch using the repository's built-in GITHUB_TOKEN.
- Runs unit tests, the collector, then the simulator every 15 minutes and on manual dispatch.
- Uploads snapshots, logs and portfolio state as a run artifact.
- Does not connect a wallet, exchange, broker or place real orders.

IMPORTANT BEFORE INSTALLING
1. This patch is prepared from the current repository version inspected on 12 Nov 2026.
2. The connected GitHub integration refused branch creation with HTTP 403 (no write permission), so these files have NOT been committed to your repository.
3. Apply the files using GitHub in your Android browser/app, or give the GitHub connection write permission and ask to apply them.
4. The workflow needs Actions enabled and GITHUB_TOKEN repository permission to write contents. The workflow declares `contents: write`. If your repository's Actions settings restrict workflow token permissions, allow read/write permissions.
5. The `mujaa-paper-state` branch contains only the virtual portfolio JSON. Do not manually delete it while preserving paper-trading continuity.
6. First successful workflow run initializes the virtual portfolio at $100. Later runs restore that state. If a run fails, do not treat missing output as zero P&L.
7. This is an experimental paper simulator, not investment advice or a guarantee of profit. Market data can be stale or manipulated; the filters are basic and must be evaluated over a long paper-only period before considering any live use.

Files to replace/add
- Replace paper_trader.py
- Replace .github/workflows/mujaa-demo.yml
- Add tests/test_paper_trader.py

No changes are required to mujaa_bot.py or requirements.txt for this patch.
