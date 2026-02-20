Five Strategies to Exploit the Consistency + MVF Edges

  1. Portfolio Replication (Mirror the Top N Traders)

  The problem with consensus: aggregating traders into one direction per market destroys individual edge. The insights
  measured trader-level profitability — 87% of 9-month-consistent traders are profitable the following month across their
  whole book.

  The fix: don't aggregate. For each of the top ~100 traders (9-month consistent, MVF < 0.10, min_markets >= 20), replicate
  their actual positions as they enter. Each trader gets a capital slice. When trader T enters market M on the YES side at
  $0.35, you buy YES at $0.35 (+delay). When they enter NO, you buy NO.

  - What it preserves: Individual trader's portfolio diversification, entry prices, position sizing intuition, the 87%
  trader-month win rate
  - What it loses: Nothing from the insights — this IS what insight #02 measured
  - Sizing: Equal-weight across traders, cap per-trader at 2% of portfolio, cap per-market at 5%
  - Rebalance: Monthly. Drop anyone who lost consistency, add new qualifiers
  - Expected performance: ~85% of traders profitable per month, median $4,691/trader/month (insight #02, 9mo/20mkt config).
  With 100 traders at $1K each → ~$470K/month on $100K deployed, but the median is the realistic anchor not the mean
  - Key risk: Capacity. If copying $10K+ per trader per market, you move prices in thin markets. Stick to markets with
  >$100K volume (insight #04)

  2. Anti-Consensus YES → Bet NO

  Insight #06 found the strongest anomaly in the entire dataset: when skilled traders consensus-point YES on a market, it
  resolves YES only 18.5% of the time vs the 38.1% base rate. This is a -19.6pp anti-signal — twice as strong as the
  positive NO-only signal (+12pp above base rate for pure_taker NO).

  The strategy: When N >= 5 skilled pure_taker traders agree the direction is YES on a market (agreement >= 60%), bet NO.

  - Why it might work: Consistently profitable traders earn most of their PnL from contrarian bets on popular markets
  (insight #04: "hard markets" have $1,492 edge). When they pile into YES on a market, they're likely buying into a
  crowd-favorite that's overpriced. The market reflexively reprices, making the YES side expensive. The NO side becomes
  cheap
  - Entry: Wait for the consensus YES signal, then buy NO at the market price. The delay benefit (insight #07) applies here
  too — wait 60s for price to stabilize
  - Why this is different from the sweep's NO-only: The sweep's NO-only uses consensus-NO as the signal. This uses
  consensus-YES as the trigger and goes the opposite direction. It's directly monetizing the anti-predictiveness of YES
  consensus
  - Expected bet count: Should be higher than the current sweep since YES consensus appears in many markets. The 18.5% HR is
   measured on ~1,400 configs with healthy sample sizes
  - Key risk: The anti-signal may be an artifact of base rate asymmetry (61.9% of markets resolve NO anyway). Need to
  confirm the 18.5% HR is statistically below the per-window NO base rate, not just below 50%

  3. Trader Entry as Market-Level Alert (No Direction)

  Stop trying to predict which direction a market resolves. Instead, use skilled trader entry as an attention signal — "this
   market has informed flow, it's worth trading" — and combine with market-level features to decide direction independently.

  Pipeline:
  1. Alert: A market gets flagged when N >= 3 consistent pure_takers have entered (any direction)
  2. Features: Compute market-level features: current price deviation from 50%, volume spike, category, time to expected
  resolution, number of traders on each side globally (not just the skilled pool)
  3. Direction model: Simple logistic regression or decision tree trained on resolved markets: given these features, does
  YES or NO win?
  4. Entry: Price band filter [0.10, 0.90], execute with 60s delay

  - Why this might beat consensus copy: It uses the consistency pool for what it's actually good at — identifying markets
  with information activity — while using a separate model for direction. The insights showed skilled traders pick good
  markets (insight #04: $255-$2,670 edge in liquid/hard/political markets) even though their consensus direction isn't
  reliably copyable
  - Data advantage: With 390K resolved markets and rich features, you have far more training data than the 10-60 bets per
  window the consensus backtester gets
  - Key risk: Lookahead in the feature model. Must be strictly trained on data before each holdout window

  4. Taker Trajectory — Catch Rising Stars Early

  Insight #03 revealed that top takers improve over time: -$290K in their early career → +$275K later. Their PnL efficiency
  goes from 14.0c to 18.6c per dollar. This means there exists a population of traders who are currently mediocre but will
  become skilled.

  The strategy: Identify traders on an upward PnL trajectory before they qualify for the consistency filter.

  - Signal: Instead of requiring N consecutive profitable months, compute a slope: regress monthly PnL against time for each
   trader. Select traders with (a) positive slope, (b) last 3 months profitable, (c) MVF < 0.10, (d) at least 6 months
  history. These are traders who haven't yet achieved 9-month consistency but are improving toward it
  - Advantage: The pool is much larger (thousands vs hundreds), the traders are less well-known (less likely to be already
  copied by others on Polymarket), and you get them before peak performance. By the time a trader hits 9-month consistency,
  their alpha may already be crowded
  - Sizing: Smaller per trader (these are higher-risk, less proven). Scale up allocation as their consistency improves
  - Rebalance: Monthly. Promote traders who achieve full consistency to strategy #1 (portfolio replication). Drop those
  whose trajectory flattens
  - Expected performance: Lower per-trader edge than the fully-consistent pool but on a much larger base. The key question
  to backtest: does trajectory predict future profitability above the base rate?
  - Key risk: Regression to mean. A 3-month winning streak with positive slope could just be luck. The min_markets filter
  mitigates somewhat

  5. Informed Market-Making on Consistency-Flagged Markets

  The insights show that makers lose $220M collectively (insight #03) because they provide liquidity without information.
  But the few informed makers who do profit earn enormous returns ($10.2M top maker). Strategy: become an informed market
  maker by using the consistency pool as your information source.

  The strategy: In markets where the consistency pool has a strong directional lean (70%+ agreement among 5+ pure_takers for
   NO), provide liquidity on the NO side.

  - Mechanism: Instead of market-buying NO at the ask, place a NO limit order slightly inside the current spread. You earn
  the spread plus directional edge
  - When the signal fires: Place a NO limit at e.g. mid-price - 1c. You get filled when YES buyers cross you. If the signal
  is correct (market resolves NO), you win on direction + spread
  - Why this is different: Every other strategy here is taking liquidity. This strategy provides it. The capacity is much
  higher because you're not paying the spread — you're earning it. The entry price is mechanically better than any taker
  strategy
  - Execution delay is free: Insight #07 showed 60-300s delay improves performance. A limit order that sits for 60-300s
  before getting filled is exactly this delay. The market-making approach naturally captures the delay benefit
  - Expected edge: Spread income (~1-3% per trade) + directional edge from informed positioning. Even if directional
  prediction is only slightly better than base rate, the spread income makes the strategy positive EV
  - Key risk: Adverse selection. If the consensus signal is wrong on a specific market, you're stuck in a losing position
  and you provided liquidity to the informed flow going the other way. The insight's -0.04 average Sharpe for the NO-only
  pure_taker segment suggests this risk is real. Must restrict to markets where the signal confidence is highest (high
  agreement, high N, reasonable entry prices)

  ---
  Ranking by Expected Value

  #: 1
  Strategy: Portfolio Replication
  Signal Quality: Highest — directly measured
  Capacity: Low ($1-10K/trader)
  Complexity: Low
  Confidence: Highest
  ────────────────────────────────────────
  #: 2
  Strategy: Anti-Consensus YES→NO
  Signal Quality: Strong anomaly (-19.6pp)
  Capacity: Medium
  Complexity: Low
  Confidence: Medium — needs base-rate control
  ────────────────────────────────────────
  #: 3
  Strategy: Market Alert + Feature Model
  Signal Quality: Untested but large N
  Capacity: High
  Complexity: High
  Confidence: Low — requires ML model
  ────────────────────────────────────────
  #: 4
  Strategy: Taker Trajectory
  Signal Quality: Theoretical
  Capacity: High (large pool)
  Complexity: Medium
  Confidence: Low — unvalidated
  ────────────────────────────────────────
  #: 5
  Strategy: Informed Market-Making
  Signal Quality: Spread + directional
  Capacity: Highest
  Complexity: Highest (infra)