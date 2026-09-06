# AI_USAGE.md

Task: Task 2 (Billing Helper, a purpose-specific agent)

## Tools and models used

| Tool or model | Used for |
|---|---|
| Gemini 3.8 Flash / Claude 3.5 Sonnet | Drafting initial state machine boilerplate, test skeletons, and sandbox client error types. |

## At least three things an AI produced that were wrong or that I changed

1. **Blind Trust in PM Spec for Immediate Downgrades**:
   - *What it produced*: The AI initially generated an agent tool call that sent `POST /subscriptions/{id}/change` with `{"effective": "now"}` whenever a customer requested a downgrade, following the PM spec statement ("Downgrades take effect immediately").
   - *Why it was wrong*: The sandbox server strictly returns `HTTP 422 invalid_effective_date`. The sandbox enforces that downgrades can only take effect at `next_cycle`. Blindly following the spec broke all downgrade flows.
   - *What I did instead*: Corrected the tool logic to enforce `effective="next_cycle"`, documented the contradiction in the Spec Issues section of `DECISIONS.md`, and added clear messaging informing the customer that their plan remains active until the end of the paid billing period.

2. **Naive Retry on HTTP 503 Timeout**:
   - *What it produced*: The AI suggested a standard exponential backoff retry loop for all HTTP 5xx responses, which re-sent the `POST /refunds` call with a new payload or key.
   - *Why it was wrong*: In the sandbox chaos mode `refund_commit_then_503`, the sandbox commits the refund to the ledger first and only then returns a 503 upstream timeout. Re-submitting a new refund duplicates the charge, moving money twice in direct violation of the brief's cardinal rule.
   - *What I did instead*: Implemented strict UUID idempotency keys. On 503, the client re-verifies the transaction with the original idempotency key and inspects the invoice refund balance before determining whether the refund was processed.

3. **Assumption of Unique Email Addresses**:
   - *What it produced*: The AI assumed `GET /customers?email={email}` returned a single customer record (`response["data"][0]`).
   - *Why it was wrong*: The sandbox reference states: "Email addresses are not unique. One person can own several workspaces." (e.g. `sam.okafor@northfield.example` owns both Northfield Pilot and Northfield Main). Taking the first index arbitrarily would modify the wrong customer workspace.
   - *What I did instead*: Added a multi-customer disambiguation state in `BillingAgent.start_conversation()`, prompting the user to select the specific workspace before loading subscriptions or invoices.

## Parts I wrote or designed without AI assistance
- The `PolicyEngine` encoding the nuances of Refund Policy v3 (14-day new subscription window vs 0-day monthly renewals vs 30-day prorated annual).
- The `Spec issues` reconciliation table identifying all 11 points of conflict in the PM brief.
- The chaos test suite and ledger reconciliation logic.
