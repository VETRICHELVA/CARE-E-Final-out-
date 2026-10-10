# CARE-E demo runbook

A click-by-click script for the three demo scenarios in `docs/specs/demo-scenarios.md`, with what to say at each step, timings and a fallback plan. Screen text in quotes is what the apps show, taken from the app code; numbers are the seed numbers from `demo-scenarios.md`.

Anything marked **(check before the demo)** could not be confirmed from the code at the time of writing (mostly because the full S20 seed and the admin metrics page were still being built). Do one full dry run with this script and strike those marks out.

---

## 1. Before the audience arrives (15 minutes)

### Start everything

One terminal each, from the repository root:

| # | Command | Why |
|---|---|---|
| 1 | `make up` | Postgres, Redis, Mosquitto |
| 2 | `make demo-reset` | Wipe the database and seed it again, times relative to now (`make migrate && make seed` on a fresh database does the same) |
| 3 | `make hub` | Hub API on :8000 |
| 4 | `make worker` | Timers, live updates in the apps, webhooks, forecasts. **Without it nothing updates live and no deadline ever passes.** |
| 5 | `pnpm --filter hospital-web dev` | http://localhost:5173 |
| 6 | `pnpm --filter supplier-web dev` | http://localhost:5174 |
| 7 | `pnpm --filter delivery-web dev` | http://localhost:5175 |
| 8 | `make ingest` | Scenario 2 only: MQTT telemetry to the hub every 2 s |
| 9 | `AI_API_KEY=… make ai` | Optional: copilot and chat ordering on :8100 |

`make demo-reset` is the reset command between runs (see §5). Check that the seed reproduced the scenario data **(check before the demo)**: Hospital A's Shortages list has no open Surgical Kit A shortage, Hospital B's Inventory shows Surgical Kit A with **1,000 kit** transferable, and Hospital B's Forecasts page lists an expiry-risk IV Cannula 20G batch. If the Forecasts page says "No forecasts yet", sign in as Hospital B's store manager and click **Run forecast now** on `/forecasts` (it should toast "Forecast N products.").

### Browser layout

Each app keeps its session per browser tab, so use one tab per person, named in the tables below. The hub allows **5 sign-ins per minute per IP address** (a sixth gets "429"), and the scenarios need about 15 people, so sign everyone in ahead of time in batches of five, a minute apart, and leave the tabs open. Sign in on each app's login page with Email, Password (`demo1234`) and **Sign in**.

| Tab | App | Email | Used in |
|---|---|---|---|
| A-store | hospital-web :5173 | `store.manager@hospital-a.demo` | S1 |
| A-approver | hospital-web | `approver@hospital-a.demo` | S1 |
| A-receiver | hospital-web | `receiver@hospital-a.demo` | S1 |
| B-store | hospital-web | `store.manager@hospital-b.demo` | S1, S3 |
| B-receiver | hospital-web | `receiver@hospital-b.demo` | — (spare) |
| C-store | hospital-web | `store.manager@hospital-c.demo` **(check before the demo)** | S2 |
| C-approver | hospital-web | `approver@hospital-c.demo` **(check before the demo)** | S2 |
| C-receiver | hospital-web | `receiver@hospital-c.demo` **(check before the demo)** | S2 |
| E-store | hospital-web | `store.manager@hospital-e.demo` **(check before the demo)** | S3 |
| E-approver | hospital-web | `approver@hospital-e.demo` **(check before the demo)** | S3 |
| E-receiver | hospital-web | `receiver@hospital-e.demo` **(check before the demo)** | S3 |
| F-store | hospital-web | `store.manager@hospital-f.demo` **(check before the demo)** | S2 |
| Y-desk | supplier-web :5174 | `supplier.desk@supplier-y.demo` **(check before the demo)** | S1 |
| Dispatcher | delivery-web :5175 | `dispatcher@swiftmed.demo` | S1, S2, S3 |
| Driver | delivery-web | `driver@swiftmed.demo` (Ravi) | S1, S2, S3 |
| Platform | hospital-web | `admin@care-e.demo` | S3 step 4 |

The Hospital C–F and Supplier Y users come from the full S20 seed; the minimal seed before it had users only for Hospital A, Hospital B, Supplier X, SwiftMed and the platform. Every org also has `admin@<org>.demo` with all of its org's capabilities, which is a quick substitute if a role tab is missing (but the story is better told with separate roles).

### Timer settings

Default time limits (business-rules.md §6), each overridable by an environment variable of the same name:

| Variable | Default | What it controls |
|---|---|---|
| `SLA_CRITICAL_RESPONSE_MINUTES` | 15 | How long a source has to answer a CRITICAL request (Scenario 1) |
| `SLA_ROUTINE_RESPONSE_MINUTES` | 240 | The same for ROUTINE (Scenarios 2 and 3) |
| `SLA_CRITICAL_HOLD_MINUTES` / `SLA_ROUTINE_HOLD_MINUTES` | 30 / 1440 | How long a tentative hold lasts |
| `SLA_CRITICAL_RECOMMENDATION_MINUTES` / `SLA_ROUTINE_RECOMMENDATION_MINUTES` | 30 / 1440 | How long a recommendation stays valid |
| `TIMER_INTERVAL_SECONDS` | 30 | How often the worker looks for passed deadlines |
| `COLDCHAIN_CONSECUTIVE_READINGS` | 2 | Readings out of (or back in) range before an excursion (or recovery) |
| `COLDCHAIN_SILENT_MINUTES` | 2 | Silence while in transit before "Device silent" |

Set them for **both** `make hub` and `make worker` (the hub stamps deadlines when it creates a request; the worker acts on them), either inline (`SLA_CRITICAL_RESPONSE_MINUTES=2 make hub`, and the same for `make worker`) or once in a root `.env` file, which the Makefile loads and exports to every target. Restart both after a change.

The scripted demo does not wait for any timer: every step is a click. Shorten the response timer only if you want to show a request expiring (Scenario 1, optional beat). Do not shorten the recommendation or hold timers below a few minutes, or the recommendation can expire while you talk ("Its validity has passed, so it can no longer be decided.").

### Running order and timings

| Part | Time |
|---|---|
| Intro: the problem and the six rules (README) | 2 min |
| Scenario 1: critical shortage, decline, purchase, short delivery | 8–10 min |
| Scenario 2: cold-chain transfer with an excursion | 6–8 min (about 2 min of it waiting on telemetry) |
| Scenario 3: expiry surplus meets a forecast stock-out | 5–7 min |
| Questions | rest |

---

## 2. Scenario 1: critical transfer, decline, purchase, short delivery

**Story:** Hospital A runs short of Surgical Kit A. Four hospitals have it on record, but only one can actually give it. That one declines, so the hub recommends buying from the fastest supplier, and a short delivery opens a residual shortage on its own.

### Step 1: Hospital A reports the shortage (tab A-store)

1. Click **Shortages**, then **New shortage**.
2. Fill in the "New shortage" dialog:
   - Product: **Surgical Kit A (SURG-KIT-A)**
   - Quantity required: **1000**
   - Usable stock on hand: **150**
   - Required by: now + 72 hours
   - Priority: **Critical**
   - Minimum shelf life (days): **30**
3. Click **Report shortage**.

**Expect:** the dialog becomes "Shortage reported" with "Shortfall, computed by the hub" **850 kit**. Click **View shortage**.

**Say:** "The app didn't do that sum. The hub works out the shortfall, 1,000 needed minus 150 usable, and starts looking for stock that can actually be moved."

### Step 2: the match run (tab A-store, shortage detail)

**Expect** on the Overview tab:
- "Latest match run #1" and "Planned: Transfer — 850 kit from Hospital B".
- Eligible table: Hospital B ranked first with **1,000 kit transferable**; Supplier Y and Supplier X follow with their offered quantities. Hospital B's ETA is about 1.3 h (10.9 km ÷ 40 km/h + 1 h handover, demo-scenarios.md; with OSRM set it follows the road distance). Supplier Y about 24 h, Supplier X about 68 h. Hospital landed cost shows "—" (rule 6: hospital costs are never shown to the requester).
- "Not eligible (3)":
  - Hospital C — Quantity: "Only 100 transferable; 850 needed"
  - Hospital D — Shelf life: "Expires in 12 days; 30 required" (reads "11 days" if the delivery estimate crosses midnight UTC, i.e. after about 05:30 IST minus the ETA; run the demo in the Indian daytime)
  - Hospital E — Authorization: "Not authorized to supply this product"
- Source requests: Hospital B, "Requested", with a countdown from **15:00 left**. There is no "Re-run match" button while the request is open.

**Say:** "Four hospitals have this kit on their shelves. Recorded isn't transferable. C has 1,400 on hand, but after reservations and safety stock only 100 can go. D's stock expires in 12 days and we need 30. E isn't authorized for this product. Only B has 1,000 genuinely transferable, so the hub asks B, with a 15-minute deadline because it's critical."

Optional, with the AI service running: click **Ask copilot**, type "Why was Hospital D rejected?" and **Ask**. The answer quotes the hub's reason and ends with a "Based on:" list of the hub reads it used. "It can only read, and every number it says has to come from the hub."

### Step 3: Hospital B declines (tab B-store)

1. Show the **Dashboard**: "Incoming requests awaiting response" lists Hospital A's request with a countdown.
2. Click **Requests**. "Awaiting response" shows Hospital A, Surgical Kit A, **850 kit**, the countdown, and **Accept** / **Decline**.
3. Click **Decline**. The dialog "Decline Hospital A's request?" says "The hub looks for another source for Hospital A without you." and has an optional reason box. For the honest-audit beat, leave the reason **empty**. Click **Decline**.

**Expect:** toast "Request declined.", "No requests awaiting your response", and the request under "Answered and closed" as "Declined".

**Say:** "B can say no, and doesn't have to say why. If they don't give a reason, the audit trail won't make one up."

Optional timer beat: if instead nobody answers, the request expires after `SLA_CRITICAL_RESPONSE_MINUTES` and the hub asks B again; only a decline excludes a source (business-rules §7).

### Step 4: the hub re-runs and recommends buying (tab A-approver)

1. Open the shortage (Shortages → the Surgical Kit A row). Tab A-store's page updates by itself if `make worker` is running.

**Expect:**
- "Latest match run #2", "A source declined · …", "Planned: Buy — 850 kit from Supplier Y". Hospital B is no longer eligible; C, D and E keep their reasons. In Source requests, B shows "Declined" with "No reason was entered."
- The decision panel: "Recommendation", badge **Buy**, "Pending", "Valid for" a 30-minute countdown, the hub's explanation, Recommended: **Supplier Y** (₹28.00 unit price, about ₹24,000 landed), Alternatives: **Supplier X** (₹14.00, about ₹12,000, ETA about 68 h).

**Say:** "This is critical, so the hub ranks by earliest arrival first. Supplier X is half the price but arrives in about 68 hours; Supplier Y in about 24, comfortably inside our 72. The cheaper option is still shown as the alternative, so the approver sees the trade-off."

### Step 5: approve the purchase (tab A-approver)

1. Click **Approve purchase**. The dialog "Approve purchase?" notes "Your decision and any reason you type are recorded in the audit trail." Click **Approve purchase**.

**Expect:** "The order has gone to the supplier." in the panel (and as a toast); the recommendation shows "Approved".

**Say:** "A person approves. The AI and the hub only recommend."

### Step 6: the supplier ships, the driver delivers (tabs Y-desk, Dispatcher, Driver)

1. **Y-desk** (supplier-web): **Purchase orders** → open the new order ("Order for Hospital A", status "Sent"). Click **Acknowledge** → confirm **Acknowledge** → "Order acknowledged." Then **Mark dispatched** → confirm **Mark dispatched** → "Order dispatched. The shipment is created."
2. **Dispatcher** (delivery-web): the **Dispatch board** ("Unassigned") shows the Surgical Kit A shipment from Supplier Y to Hospital A. Click **Assign**; Driver **Ravi (+91 90000 00001)**, Vehicle **KA-01-SM-0001**; click **Assign** → "Shipment assigned. The hub worked out the route and ETA." Open the shipment to show the map, ETA and "Route" ("road route" with OSRM, "straight-line estimate" without).
3. **Driver** (delivery-web, "My jobs"): on the job, click **Picked up** → confirm → "Pickup recorded."; **In transit** → "Marked in transit."; **Delivered** → "Delivery recorded."

**Say:** "Each organization sees only its own part: the supplier sees the order, not the hospitals that were rejected; the carrier sees the job. The driver's buttons only allow the next legal step."

### Step 7: a short delivery (tab A-receiver)

1. **Deliveries** → the Surgical Kit A row shows "Delivered" → click **Receive**.
2. "Record what arrived", "Expected (from the shipment)" **850 kit**. Enter Received **790**, Accepted **790**, Rejected **0**, Condition **Good**, "Expiry date of accepted stock" any date about a year out. Leave the inspection note empty (no excursion on this shipment). Click **Record receipt**.

**Expect:** toast "Receipt recorded." and the "Receipt recorded" card: "Reconciled: the shortage is partially resolved. This delivery was 60 kit short of the 850 kit expected." A receiver cannot read shortages, so the card says "A residual shortage was opened for the rest; your organization's shortage managers can see it."

3. Switch to **A-approver**: the shortage now shows "Partially resolved". Go to **Shortages** and open the new Surgical Kit A shortage: "Shortfall (computed by the hub)" **60 kit**, "Residual of" linking to "An earlier shortage", and its own "Latest match run #1" already run. Hospital B stays excluded because it declined; which source the residual picks (Hospital C's 100 transferable now covers 60) is the hub's call **(check before the demo)**.

**Say:** "Nobody has to notice the missing 60. The hub reconciles what arrived against what was expected and opens a residual shortage that starts matching on its own."

### Step 8: the audit trail (tab A-approver)

1. On the original shortage, click the **Audit trail** tab.

**Expect:** every step in order: shortage created, match runs, the source request to Hospital B (Requested → Declined, by "Hospital B" with an "Other organization" badge, and **"System" "No reason was entered."**), the recommendation, its approval, the purchase order, the shipment's steps, the receipt and the reconciliation. If B had typed a reason, that row shows a "User reason" badge with B's words instead.

**Say:** "Every state change writes an append-only audit row. Where a person gave no reason, the system says exactly that, and it never invents a physical event."

---

## 3. Scenario 2: cold-chain transfer with an excursion

**Story:** a temperature-sensitive diagnostic kit is moved between hospitals in a cold box. The box warms up on the way; everyone is alerted, the record is kept, and the receiving hospital must inspect before accepting.

Before you start: `make ingest` is running, and the simulator is ready in a terminal (do not start it yet).

### Step 1: Hospital C reports (tab C-store)

**Shortages** → **New shortage**: Product **Rapid Diagnostic Kit (DIAG-RDK)**, Quantity required **200**, Usable stock on hand **0**, Required by now + 48 hours, Priority **Routine**, Minimum shelf life **60** (the product's default; leaving it blank gives the same) → **Report shortage**. Shortfall **200 kit** → **View shortage**.

**Expect:** "Planned: Transfer — 200 kit from Hospital F"; Hospital F eligible with **500 kit transferable** **(check before the demo** that F ranks first, and that the cold-chain gate passes; it needs a cold-chain vehicle on record, which the seed provides). Source requests: Hospital F, "Requested", with a 4-hour countdown.

**Say:** "This kit must stay between 2 and 8 °C. The hub only considers it because a cold-chain vehicle exists in the network; otherwise it would be rejected on the cold-chain gate."

Optional, with the AI service: draft the same shortage in the copilot's **Order** mode, e.g. "We need 200 rapid diagnostic kits within 48 hours, routine" → **Draft order**. The card shows every field for checking, and nothing is created until you click **Create shortage**.

### Step 2: F accepts, C approves (tabs F-store, C-approver)

1. **F-store**: **Requests** → **Accept** → dialog "Accept Hospital C's request?" ("The hub places a tentative hold on 200 kit of your transferable stock, earliest expiry first.") → **Accept and hold** → toast "Accepted. 200 kit are on hold."
2. **C-approver**: open the shortage. Decision panel badge **Transfer**, Hospital F, cost "—" (not shown when a hospital source is involved). Click **Approve transfer** → confirm **Approve transfer**.

**Expect:** "Stock is now held at the source."

**Say:** "Accepting puts a tentative hold on F's stock so nobody else can be promised it; approving makes the hold firm and creates the shipment."

### Step 3: dispatch with the cold box (tab Dispatcher)

1. **Dispatch board** → the Rapid Diagnostic Kit shipment (cold-chain flag) → **Assign**. The Vehicle list offers only cold-chain vehicles ("This shipment needs a cold-chain vehicle; only those are listed."): choose **KA-01-SM-0002 (cold chain)** and driver **Ravi** → **Assign**.
2. Open the shipment. In the cold box panel click **Attach cold box**, choose **cb-01**, **Attach** → "Cold box attached." (Alternatively **Fleet** → Cold boxes → cb-01 → **Attach to shipment**.)
3. **Driver** tab: **Picked up**, then **In transit**.

### Step 4: the excursion (terminal, then tabs Dispatcher and C-approver or C-receiver)

Start the simulator from the repository root:

```sh
uv run scripts/simulate_telemetry.py --device cb-01 --profile excursion --interval 5
```

Flags (from the script): `--device` (required), `--profile normal|excursion|silent` (default normal), `--interval` seconds between readings (default 10, minimum 1), `--host` (default localhost), `--port` (default 1883). The excursion profile publishes normal readings (3.5–5.5 °C) for 60 s, then **9.1 °C** and **9.4 °C**, then normal readings until you stop it.

**Expect**, within a few seconds of the 9.4 °C reading (about 70 s after start at `--interval 5`):
- delivery-web (carrier) and hospital-web (Hospital C, inbound only) show an error toast **"Temperature excursion (cb-01)"**: "Readings out of range: 9.4 °C, above the maximum of 8 °C." with a **View** button. The toast stays until dismissed.
- The shipment's **Cold chain** panel (delivery-web shipment detail; hospital-web Deliveries → the delivery) shows the chart with the 2–8 °C band shaded and the two out-of-range readings marked, "Allowed range" 2–8 °C, the latest reading, "battery 82%", and the event list.

**Say:** "The box publishes a reading every few seconds over MQTT. Two readings in a row outside 2–8 °C make an excursion. The hub raises it, writes it to the audit trail of every organization involved, and alerts the carrier and the receiving hospital live."

### Step 5: recovery (same screens)

Two normal readings later (about 10 s at `--interval 5`):

**Expect:** toast "Back in range (cb-01)": "Readings back in range, latest … °C. The excursion stays on record." The Deliveries list badge becomes **"Excursion on record"**.

**Say:** "It's back in range, but the excursion isn't erased. It stays on the record."

Then on the **Driver** tab click **Delivered**, and stop the simulator with Ctrl-C. (Stop it only after Delivered: while the shipment is in transit, 2 minutes without a reading raises "Device silent".)

### Step 6: receiving needs an inspection note (tab C-receiver)

**Deliveries** → the Rapid Diagnostic Kit row → **Receive**.

**Expect:** a red notice "A cold-chain excursion is on record for this shipment." ("Inspect the stock and record what you found in the inspection note before accepting any of it."), the field **"Inspection note (required)"**, and the Cold chain panel below the form.

1. Enter Received **200**, Accepted **200**, Rejected **0**, Condition **Good**, an expiry date, and leave the inspection note empty → **Record receipt**: the form refuses with "Enter an inspection note: a cold-chain excursion is on record for this shipment." (the hub refuses it too).
2. Type a note, e.g. "Indicator strips normal, packaging intact; 9.4 °C for about 10 s." → **Record receipt** → "Reconciled: the shortage is resolved."

**Say:** "The hub won't let anyone accept this stock without saying what they inspected."

---

## 4. Scenario 3: expiry surplus meets a forecast stock-out

**Story:** Hospital B has IV cannulas that will expire before it can use them; Hospital E is forecast to run out in four days. The hub connects them before either becomes a problem.

### Step 1: B's expiry risk (tab B-store)

1. **Dashboard** → "Forecasts and expiry risk": "Expiry-risk batches" **1** and "Offer **300 each** to the network: IV Cannula 20G, batch …" with a "Synthetic history" badge. The seed history is built for an excess of about 300; the hub's test accepts 300 ± 5, and the S18 build saw 296–298 depending on the weekday **(check before the demo)**.
2. Click **Forecasts** (or **View all**). "Expiry-risk batches" lists the batch (on hand 1,000, safety stock 200, expiry +55 days). Click **Offer to network** → dialog "Offer 300 each to the network?" ("Other hospitals see the quantity, an expiry band and your location, never the batch or its expiry date. The hub offers no more than the batch's transferable stock.") → **Offer to network** → "Offered to the network." The post appears under "Your surplus posts".

**Say:** "The forecast says B will use about 500 of these 1,000 before they expire, and B keeps 200 as safety stock. That leaves about 300 that will otherwise be thrown away."

### Step 2: E sees the stock-out and the surplus (tab E-store)

**Dashboard**:
- "Forecasts and expiry risk" → "Predicted stock-outs": **IV Cannula 20G**, date "(in 4 days)".
- "Surplus offered to you": "Hospital B offers 300 each IV Cannula 20G", "Expires in 30–59 days · Matches your predicted stock-out on …".

**Say:** "E uses about 30 a day and has 120 left: four days. E sees B's offer, but only the quantity, an expiry band and the location, never B's batch details. That's rule 6."

### Step 3: E's shortage, matched to B (tabs E-store, B-store, E-approver, Dispatcher, Driver, E-receiver)

1. **E-store**: **Shortages** → **New shortage**: IV Cannula 20G, Quantity required **300**, Usable stock on hand **0** (so the shortfall is 300, as in the hub's Scenario 3 test), Required by now + 48 hours, Priority **Routine** → **Report shortage** → **View shortage**.
   **Expect:** Hospital B ranked first ("Planned: Transfer — 300 each from Hospital B"): near-expiry stock wins the tie, and it is the lowest landed cost.
2. **B-store**: **Requests** → **Accept** → **Accept and hold**.
3. **E-approver**: the shortage's decision panel, **Transfer** from Hospital B → **Approve transfer** → "Stock is now held at the source."
4. **Dispatcher**: assign Ravi and **KA-01-SM-0001**. **Driver**: **Picked up**, **In transit**, **Delivered**.
5. **E-receiver**: **Deliveries** → **Receive**: Received 300, Accepted 300, Rejected 0, Good, an expiry date → **Record receipt** → "Reconciled: the shortage is resolved."

**Say:** "Ranking prefers the batch that's closest to expiring, so the stock that would have been wasted is used first."

### Step 4: the network view (tab Platform)

Sign in to hospital-web as `admin@care-e.demo` and open **/admin**: the network metrics show procurement cost avoided (against the cheapest supplier price) and **300 units saved from expiry**, plus median time from shortage to confirmed source, transfer vs purchase share and cold-chain compliance. **(check before the demo**: the `/admin` page and `GET /metrics/network` are S20 work that was not built when this runbook was written; confirm the page, its labels and the figures.**)**

**Say:** "Across the network: what we didn't have to buy, and what didn't expire."

---

## 5. Fallback plan

| Problem | What to do |
|---|---|
| **No cold-box hardware, or it won't connect** | Use the simulator; it publishes exactly what the firmware does: `uv run scripts/simulate_telemetry.py --device cb-01 --profile excursion --interval 5`. The real box needs `firmware/cold-box/include/secrets.h` with `DEVICE_ID` `cb-01`, and Mosquitto is bound to 127.0.0.1, so it needs the temporary `socat` LAN forward in `firmware/cold-box/README.md`. The hardware test has never been run (parts had not arrived), so default to the simulator. |
| **No excursion alert** | Check, in order: `make ingest` is running (it prints posted batches); `cb-01` is attached to the shipment (shipment detail, cold box panel); the shipment is Assigned, Picked up or In transit (readings link only then); `make worker` is running (it publishes the live event). Readings from an unregistered device id are ignored. |
| **Want to show "Device silent"** | With the shipment In transit, run the simulator with `--profile silent`; it stops after 60 s, and "Device silent" follows about 2–2.5 minutes later (`COLDCHAIN_SILENT_MINUTES`, worker tick 30 s). |
| **The worker isn't running or crashed** | Nothing updates live, and no deadline or hold expires. Restart `make worker`; meanwhile reload pages by hand (every screen reads current state from the hub on load). |
| **OSRM is not running** | Nothing to do: the hub falls back to straight-line distance × 1.3 and the shipment shows "straight-line estimate" instead of "road route". ETAs and transport costs differ slightly from a road-routed run. OSRM is optional (`infra/osrm/README.md`); it was never exercised against a real server in development. |
| **No AI key, or the AI service is down** | Skip the copilot beats. The panel says "AI is not configured." (no key) or that the copilot is not available; everything else works. Say: "The AI is an assistant, so the system works without it." |
| **"429" on sign-in** | The 5-per-minute sign-in limit. Wait a minute. Sign everyone in before the demo. |
| **A recommendation expired while talking** | "Its validity has passed, so it can no longer be decided." The worker expires it and re-runs matching, which makes a new recommendation; open the shortage again. Avoid it by not shortening `SLA_*_RECOMMENDATION_MINUTES`. |
| **"11 days" instead of "12 days" for Hospital D** | The delivery estimate fell on the next UTC day. Harmless; explain, or run the demo in daytime IST. |
| **Data from an earlier run in the way** | `make demo-reset` (wipes and re-seeds, times relative to now). Takes under a minute **(check before the demo)**. Re-sign-in afterwards: old sessions belong to users that no longer exist. |
| **Something else breaks mid-demo** | Switch to the recorded video (below), then reset with `make demo-reset` before taking questions on the live system. |

### Recorded video

Record a full dry run of all three scenarios (screen plus voice-over from this script) the day before, after the "check before the demo" items are confirmed, and keep it on the presenting laptop, not only online. Cut it into one clip per scenario so you can jump to the one that failed.

### Between runs

1. Stop the simulator (Ctrl-C) if it is still running.
2. `make demo-reset`.
3. Close the app tabs and sign everyone in again (mind the 5-per-minute limit).
4. If you changed any `SLA_*` or `COLDCHAIN_*` variables, put them back and restart `make hub` and `make worker`.

### Automated check

`make e2e` drives the Playwright tests in `e2e/` against the seeded system (it starts the hub and the apps unless they are running; it does not start `make worker`, which live updates need). Run it the day before to catch regressions. It expects freshly seeded data (the seeds only add what is missing and never reset stock), changes the data and counts against the sign-in limit, so run `make demo-reset` before and after it.
