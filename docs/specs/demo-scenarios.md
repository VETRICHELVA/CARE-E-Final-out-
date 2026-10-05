# Demo scenarios and seed data

`make seed` must reproduce these numbers exactly, with all times relative to "now" when the seed runs. Seed scripts live in `scripts/seed/` and are idempotent (running twice gives the same state).

## Organizations and users
| Org | Type | Notes |
|---|---|---|
| Hospital A, Hospital B, Hospital C, Hospital D, Hospital E, Hospital F | HOSPITAL | Each has one facility; C and F have cold storage |
| Supplier X, Supplier Y, Supplier Z | SUPPLIER | |
| SwiftMed Logistics | LOGISTICS | 2 drivers (Ravi, Priya), 2 vehicles (one cold-chain) |
| CARE-E Platform | PLATFORM | Admin user |

One user per role per org, emails like `approver@hospital-a.demo`, password `demo1234` (development only). Hospital locations are 5–40 km apart within one city.

Products: about 40, including **Surgical Kit A** (no cold chain, min shelf life 30 days), **Rapid Diagnostic Kit** (2–8 °C), and **IV Cannula 20G**.

ProductAuthorization: every hospital and supplier is authorized for every product, **except Hospital E for Surgical Kit A**.

## Scenario 1 — Critical transfer, decline, purchase, short delivery
Seed state for Surgical Kit A:

| Source | Stock | Expiry | Last verified | Expected result |
|---|---|---|---|---|
| Hospital B | on hand 2,500, reserved 800, allocated 200, safety 500 → **1,000 transferable** | +180 days | 2 h ago | Eligible, ranked first (ETA about 6 h) |
| Hospital C | on hand 1,400, reserved 800, safety 500 → **100 transferable** | +200 days | 3 h ago | Fails quantity: "Only 100 transferable; 850 needed" |
| Hospital D | 900 transferable | **+12 days** | 1 h ago | Fails shelf life: "Expires in 12 days; 30 required" |
| Hospital E | 1,200 transferable | +150 days | 1 h ago | Fails authorization |
| Supplier X | ₹14.00/unit, lead time 66 h, 5,000 available | — | today | Eligible, ETA about 68 h, about ₹12,000 |
| Supplier Y | ₹28.00/unit, lead time 22 h, 2,000 available | — | today | Eligible, ETA about 24 h, about ₹24,000 |
| Supplier Z | no offer for this product | — | — | Not a candidate |

Steps:
1. Hospital A creates a shortage: Surgical Kit A, required 1,000, local usable 150, CRITICAL, required by now + 72 h, min shelf life 30 days. The hub shows shortfall **850**.
2. Matching chooses TRANSFER from Hospital B, so the hub sends B a source request with a 15-minute response deadline. The match run shows C, D and E as rejected with their reasons.
3. Hospital B **declines** (with or without a reason). B's request becomes DECLINED and matching re-runs without B.
4. C, D and E still fail their gates, so the recommendation is **BUY from Supplier Y** (earliest ETA, because the shortage is CRITICAL), with Supplier X as the alternative.
5. Hospital A's approver clicks "Approve purchase" and sees "The order has gone to the supplier."
6. Supplier Y acknowledges and dispatches; a driver is assigned, picks up and delivers.
7. Hospital A records received 790, accepted 790, rejected 0. The shortage becomes PARTIALLY_RESOLVED and a **residual shortage of 60** opens and starts matching.
8. The audit trail shows every step, with B's decline marked USER or SYSTEM depending on whether a reason was typed.

## Scenario 2 — Cold-chain transfer with an excursion
1. Hospital C creates a ROUTINE shortage: Rapid Diagnostic Kit, required 200, local usable 0, required by now + 48 h, min shelf life 60 days.
2. Hospital F has 500 transferable (expiry +240 days, verified today) and is recommended. F accepts; C approves the transfer.
3. The dispatcher assigns the cold-chain vehicle and device `cb-01`.
4. Telemetry from the real box, or `simulate_telemetry.py --profile excursion`, shows readings near 4 °C, then 9.1 °C and 9.4 °C → a **coldchain.excursion** alert in the delivery and hospital apps.
5. Readings return to range → RECOVERED, but the excursion stays on record.
6. Hospital C's receive screen requires an inspection note before it accepts the stock.

## Scenario 3 — Expiry surplus meets a forecast stock-out
Seed state for IV Cannula 20G:
- Hospital B: one batch, on hand 1,000, safety stock 200, expiry +55 days. History gives forecast usage before expiry of about 500 → **expiry-risk excess 300**.
- Hospital E: usable stock 120; 12 months of history averaging about 30 per day → **predicted stock-out in 4 days**.

Steps:
1. The forecast job flags B's batch; B's dashboard suggests "Offer 300 to the network", and B posts the surplus.
2. E's dashboard shows the predicted stock-out and the matching surplus from Hospital B (`surplus.matched`).
3. E creates a ROUTINE shortage for 300; matching ranks B first (near-expiry tiebreak, lowest landed cost); the transfer completes.
4. The admin metrics dashboard shows procurement cost avoided (vs the cheapest supplier price) and 300 units saved from expiry.

## Synthetic consumption history
- 12 months of daily records per hospital × product for the 15 most-used products.
- Base rate by product and hospital size, weekly seasonality (weekdays about 1.2× weekends), ±15% noise, and 3–5 random spikes of 2–3× per series. Use a fixed random seed.
- Scenario 3's two series are generated so the numbers above hold.
