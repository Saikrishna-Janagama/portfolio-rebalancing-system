# Synthetic Portfolio Rebalancing

Educational simulation only. No investment advice, external market feeds, or personal customer data.

## Run
Python 3.10+; standard library only.

Starter: `python main.py --out starter --portfolios 20 --instruments 100 --days 12 --verify-repeat`

Full: `python main.py --out output --portfolios 1000 --instruments 5000 --days 250 --verify-repeat`

## Workflow
Generate investor, portfolio and instrument masters; holdings and dated prices; targets, fee schedules and constraints; then evaluate drift, produce policy-constrained trade proposals, and simulate allocation after fees. Persist proposals, trades, bridge rows, allocations and creation events. Export all tables as CSV, plus SQLite and SQL DDL. The five negative cases are documented separately in `ground_truth.csv` and `ground_truth.json` and are **not inserted as invalid operational trades**.

## Rules
Targets (equity/bond/cash/commodity): conservative 25/60/10/5, balanced 50/35/10/5, aggressive 75/15/5/5. Drift trigger: maximum absolute asset-class deviation >3 percentage points. Trades must be >=$100, within instrument liquidity limit, not excluded, and sells cannot exceed quantity held. Proceeds from sells fund buys, after estimated fees. Fee: $1 + notional times tier rate (0.1% below $1k; 0.06% below $10k; 0.03% thereafter), plus 0.02% for commodity. Missing latest prices are excluded from the *priced subset*, with reason MISSING_PRICE_PARTIAL; this is a partial valuation, not a full-portfolio valuation. Price dates are business weekdays, not exchange-specific holidays.

## Data model and cardinality
InvestorProfile 1:N Portfolio; Portfolio 1:N Holding; Instrument 1:N Holding (Portfolio N:M Instrument via Holding); Instrument 1:N MarketPrice; Portfolio 1:N TargetAllocation; Portfolio 1:N RebalanceProposal; RebalanceProposal 1:N Trade and 1:N ProposalAllocation; RebalanceProposal N:M Trade via ProposalTrade (trade currently belongs to one proposal); Portfolio 1:N Constraint, optional Instrument 1:N Constraint. FeeSchedule applies by asset class and notional tier. EventLog stores append-only proposal creation events. Effective dates are on targets, fees and constraints; created/as-of dates are on other entities. Statuses: ACTIVE masters; PROPOSED/NO_TRADE proposals; PROPOSED trades. No execution or settlement is simulated.

## Data dictionary
All IDs are unique, required TEXT primary keys. Foreign keys are required unless marked optional. Monetary amounts are synthetic USD, quantity is units, weights/rates are fractions, dates are ISO-8601 YYYY-MM-DD. SQL CHECK, FK and UNIQUE constraints provide field-level validation.

| Entity | Fields (types; optional marked ?) | Validation / meaning |
|---|---|---|
| InvestorProfile | id TEXT, risk_profile TEXT, created_on DATE, status TEXT | risk_profile in CONSERVATIVE/BALANCED/AGGRESSIVE; status ACTIVE |
| Portfolio | id TEXT, investor_id FK, created_on DATE, status TEXT, base_currency TEXT | investor exists; ACTIVE, USD |
| Instrument | id TEXT, symbol TEXT, asset_class TEXT, liquidity_limit REAL, status TEXT | symbol unique; asset in EQUITY/BOND/CASH/COMMODITY; limit >=0 USD; ACTIVE |
| Holding | id TEXT, portfolio_id FK, instrument_id FK, quantity REAL, as_of DATE | quantity >=0; (portfolio,instrument) unique |
| MarketPrice | id TEXT, instrument_id FK, price_date DATE, price REAL, currency TEXT | price >0 USD; (instrument,date) unique |
| TargetAllocation | id TEXT, portfolio_id FK, asset_class TEXT, weight REAL, effective_from DATE, effective_to DATE? | weight 0..1; four weights sum to 1; end >= start if supplied |
| FeeSchedule | id TEXT, asset_class TEXT, tier_min REAL, tier_max REAL?, rate REAL, flat_fee REAL, effective_from DATE | nonnegative USD bounds and fractional rate; tier_max > tier_min if supplied |
| Constraint | id TEXT, portfolio_id FK?, instrument_id FK?, constraint_type TEXT, threshold REAL?, effective_from DATE, active INTEGER | MIN_TRADE or EXCLUDED; active 0/1; MIN_TRADE threshold USD |
| RebalanceProposal | id TEXT, portfolio_id FK, as_of DATE, status TEXT, portfolio_value REAL, estimated_fees REAL, maximum_drift_before REAL, maximum_drift_after REAL, reason TEXT | status PROPOSED/NO_TRADE; values >=0 USD; drift fractions 0..1 |
| Trade | id TEXT, proposal_id FK, portfolio_id FK, instrument_id FK, side TEXT, quantity REAL, price REAL, notional REAL, fee REAL, status TEXT, created_on DATE | side BUY/SELL; quantity/price positive; notional = quantity*price within rounding; PROPOSED |
| ProposalTrade | proposal_id FK, trade_id FK | unique trade; bridge PK (proposal,trade) |
| ProposalAllocation | proposal_id FK, asset_class TEXT, weight_before REAL, weight_after REAL, target_weight REAL | one per asset; allocation sums to 1 for priced holdings |
| EventLog | id TEXT, entity_type TEXT, entity_id TEXT, event_type TEXT, event_date DATE, details JSON TEXT | append-only CREATED events |
| AnomalyCase | id TEXT, proposal_id FK, issue_type TEXT, ground_truth TEXT, details JSON TEXT | 5 labelled negative scenarios; ground truth exported separately |

## Validation and metrics
`validation_summary.json` contains row counts, FK integrity, weight reconciliation, excluded-trade violations, oversells, fee mismatches, trade arithmetic, drift improvement count, missing prices, and reproducibility (CSV SHA-256 across two seeded runs). `source_manifest.json` documents provenance. For reproducibility, run with `--verify-repeat`.

## Known limitations
The rebalancing heuristic is greedy and may not minimize drift or fees globally. Cash is modelled as an asset class and also as uninvested trade proceeds; this is a simplification. Some proposals can increase maximum drift due to trade minimums, fees and liquidity. Negative scenarios are **labelled hypothetical cases**, not physically malformed input rows. The generator does not currently generate executable invalid candidate trades, price-as-of overrides, full lifecycle execution/settlement events, or independent JSON exports of every table. For production use, add optimization, corporate actions, multi-currency pricing, full historical holdings, transaction accounting and compliance review.

## Additional acceptance checks
After running `main.py`, run `python complete.py --out output` to export each database entity as JSON, create a field-level data dictionary, and generate ten isolated candidate tests (five invalid, five valid). `candidate_inputs.json` contains only test inputs; `ground_truth_injected.json` and `.csv` contain expected decisions and must not be used as model input. `additional_validation.json` reports confusion-matrix counts, precision, recall and accuracy. `review_events.json` contains append-only simulated policy-review transitions. These are isolated review tests, not actual executed transactions. The generated outputs are synthetic and educational, not financial advice.
