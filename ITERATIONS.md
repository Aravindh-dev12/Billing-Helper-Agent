# ITERATIONS.md

Task: Task 2 (Billing Helper, a purpose-specific agent)

## Entries

### 2026-09-06 Iteration 1: Handling 503 Timeout in Chaos Mode
- **Built or changed**: Initial implementation called `POST /refunds` with a simple retry on error.
- **Observed (evidence)**: In chaos mode `refund_commit_then_503`, the sandbox processed the first refund and returned HTTP 503. A naive retry with a new key created a second duplicate refund in the ledger.
- **Concluded**: When interacting with financial endpoints, network timeouts do not mean failure. Every write must carry a unique idempotency key, and timeouts must re-verify the transaction status rather than issuing a new call.
- **Next**: Added deterministic UUID `Idempotency-Key` headers on all POST requests. On 503, the client queries the invoice state and replays the exact same idempotency key. Verified with `test_refund_commit_then_503_chaos_mode` that exactly 1 refund is recorded in the ledger.

### 2026-09-06 Iteration 2: Downgrade Failure (HTTP 422 Invalid Effective Date)
- **Built or changed**: Attempted to implement the PM spec requirement: "Downgrades take effect immediately."
- **Observed (evidence)**: Sandbox returned `HTTP 422 invalid_effective_date: Downgrades cannot take effect immediately`.
- **Concluded**: The PM spec directly conflicts with the billing system architecture. Downgrades can only take effect at `next_cycle`.
- **Next**: Resolved the spec contradiction in `DECISIONS.md`. Updated `_handle_downgrade_intent()` to enforce `effective="next_cycle"` and explain to the customer that their plan remains active until the end of the paid cycle.

### 2026-09-06 Iteration 3: Renewal Refund Policy Enforcement
- **Built or changed**: Evaluated refund requests for monthly customers.
- **Observed (evidence)**: Test `test_monthly_renewal_non_refundable` originally failed on `cust_0001` because Maya Chen held a `new_subscription` invoice rather than a `renewal`.
- **Concluded**: `cust_0002` (Daniel Okoro) has active monthly renewals (`FW-2026-01003`). The policy engine must differentiate invoice kinds: new subscriptions have a 14-day window; monthly renewals are non-refundable under Policy Section 1.2.
- **Next**: Updated test fixture to target Daniel Okoro and verified that renewal refunds are politely declined with policy rationale and cancellation options.
