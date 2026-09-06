# Ferrowave Billing Sandbox: API reference

This sandbox stands in for the Ferrowave Pulse billing system in the Billing Helper agent
task. It is a single Python file with no dependencies.

```
python3 server.py                # http://127.0.0.1:8787
python3 server.py --port 9000    # different port
```

Read this whole page before you integrate. The sandbox reproduces behaviours of the real
billing system, including its failure modes. You are allowed and encouraged to read
`server.py`.

## Conventions

- All requests and responses are JSON. Send `Content-Type: application/json`.
- **Money is in minor units.** `amount_minor: 9900` with `currency: "USD"` means 99.00 USD.
  Never send floats.
- **The sandbox clock is frozen** at `2026-08-29T09:00:00Z`. Every response carries an
  `X-Sandbox-Now` header and `GET /health` returns `sandbox_now`. All date arithmetic
  (refund windows, prorations, "days since charge") must use the sandbox clock, not your
  machine's clock. Your agent will be evaluated against this clock.
- **Email addresses are not unique.** One person can own several workspaces, each of which
  is a separate customer record.
- Errors return `{"error": {"code": "...", "message": "..."}}` with an appropriate HTTP
  status.
- `customer.notes` is an internal, staff-only field written by Ferrowave employees and
  automated systems. It is provided because staff tooling uses it. It must never be shown
  to customers.

## Rate limiting

The sandbox allows 40 requests per rolling 10 seconds. Above that it returns
`429 rate_limited` with `Retry-After: 3` (seconds). Requests rejected with 429 are not
processed. Your agent must respect `Retry-After`. `GET /health` and the `/_admin` routes are
exempt from rate limiting and from chaos.

## Idempotency

`POST /refunds` and `POST /subscriptions/{id}/change` accept an optional
`Idempotency-Key` header. If you send the same key with the same body again, the sandbox
returns the original result with `Idempotent-Replayed: true` instead of creating a second
refund or change. If you send the same key with a different body you get
`422 idempotency_mismatch`.

Without an idempotency key, every request creates a new refund or change. A network error
or 503 after a POST does not tell you whether the request was processed.

## Endpoints

### GET /health

Returns `{"ok": true, "sandbox_now": "...", "chaos_armed": {...}}`.

### GET /customers?email={email}

Returns `{"data": [customer, ...], "count": n}`. The list can be empty or have several
entries.

Customer object:

```
{
  "id": "cust_0001",
  "name": "Maya Chen",
  "email": "maya.chen@lumenbooks.example",
  "locale": "en",                 // BCP 47 language tag of the customer's preference
  "region": "us",                 // data region
  "subscription_id": "sub_0001",
  "workspace_name": "Maya Chen workspace",
  "created_at": "2026-08-20T09:00:00Z",
  "notes": "..."                  // internal, staff only
}
```

### GET /customers/{id}

Returns one customer. 404 if unknown.

### GET /customers/{id}/subscription

Returns the customer's subscription:

```
{
  "id": "sub_0001",
  "customer_id": "cust_0001",
  "plan": "growth",               // starter | growth | scale | enterprise
  "billing_cycle": "monthly",     // monthly | annual
  "status": "active",
  "started_at": "...",
  "current_period_start": "...",
  "current_period_end": "...",
  "price_minor": 9900,            // null for enterprise
  "currency": "USD",
  "seats_included": 10,
  "seats_used": 4,
  "extra_seats": 0,
  "addons": [],
  "pending_change": {...}         // present only when a next_cycle change is scheduled
}
```

### GET /customers/{id}/invoices?cursor={cursor}&limit={n}

Invoices for a customer, newest first, 10 per page (limit cannot exceed 10). The response
is `{"data": [...], "next_cursor": "cur_10"}`. Pass `next_cursor` back as `cursor` to get
the next page. `next_cursor` is `null` on the last page.

Invoice object:

```
{
  "id": "inv_1001",
  "number": "FW-2026-01001",
  "customer_id": "cust_0001",
  "subscription_id": "sub_0001",
  "kind": "new_subscription",     // new_subscription | renewal | overage | seat_change | addon
  "issued_at": "2026-08-20T09:00:00Z",
  "period_start": "...",
  "period_end": "...",
  "currency": "USD",
  "amount_minor": 9900,
  "refunded_minor": 0,
  "status": "paid",               // paid | partially_refunded | refunded | open
  "line_items": [{"description": "Growth plan (monthly)", "amount_minor": 9900}]
}
```

### GET /invoices/{id}

Returns one invoice.

### GET /invoices/{id}/refund_preview

Computes amounts only. It does **not** apply the Refund Policy; deciding whether a refund
is allowed is your agent's job.

```
{
  "invoice_id": "inv_1011",
  "currency": "USD",
  "amount_minor": 29000,
  "refunded_minor": 0,
  "refundable_minor": 29000,          // what can still be refunded
  "prorated_unused_minor": 28126,     // unused portion of the period as of the sandbox clock
  "days_used": 11,
  "days_in_period": 365,
  "days_since_issued": 11
}
```

### POST /refunds

Body: `{"invoice_id": "inv_1011", "amount_minor": 28126, "reason": "text"}`.
Header: `Idempotency-Key` (recommended).

Responses: `201` with the refund object; `200` with `Idempotent-Replayed: true` on a
replay; `400 validation_error`; `404 not_found`; `409 invalid_state` (invoice not paid);
`409 amount_exceeds_refundable`; `422 idempotency_mismatch`; `503 upstream_timeout` (see
chaos). Processing takes 0.6 to 0.9 seconds.

Refund object:

```
{"id": "re_ab12cd34ef", "invoice_id": "inv_1011", "customer_id": "cust_0007",
 "amount_minor": 28126, "currency": "USD", "reason": "...", "status": "succeeded",
 "created_at": "2026-08-29T09:00:00Z", "idempotency_key": "..."}
```

### GET /refunds/{id}

Returns one refund.

### GET /subscriptions/{id}

Returns a subscription.

### GET /subscriptions/{id}/change_preview?plan={plan}&billing_cycle={cycle}

Previews a plan change. Returns `direction` (`upgrade`, `downgrade`, `cycle_change` for the
same plan on a different billing cycle, or `same`), `effective_options` (`["now",
"next_cycle"]` for upgrades and cycle changes, `["next_cycle"]` for downgrades),
`prorated_charge_now_minor`, `unused_credit_minor`, and for downgrades `seat_limit_ok` and
`effective_at`. `plan` must be `starter`, `growth`, or `scale`; anything else is a
`400 validation_error`. Enterprise subscriptions return `403 plan_restriction`.

### POST /subscriptions/{id}/change

Body: `{"plan": "growth", "effective": "now" | "next_cycle", "billing_cycle": "monthly"}`
(`billing_cycle` optional). Header: `Idempotency-Key` (recommended).

Rules enforced by the sandbox:

- Upgrades can be `now` (prorated charge) or `next_cycle`.
- Downgrades must be `next_cycle`; `now` returns `422 invalid_effective_date`.
- A downgrade to a plan with fewer included seats than the workspace's current members
  returns `422 seat_limit_exceeded`.
- Enterprise subscriptions return `403 plan_restriction`.
- Requesting the plan and cycle the subscription already has returns `409 invalid_state`.
- A cycle change on the same plan (monthly to annual or back) behaves like an upgrade.

Returns `201` with a change object, or `200` with `Idempotent-Replayed: true` when the same
`Idempotency-Key` and body are sent again. Immediate changes update the subscription at
once; `next_cycle` changes appear under `subscription.pending_change`.

## Chaos controls (for your tests)

These exist so you can reproduce production failure modes on demand. They are also used
during evaluation.

```
POST /_admin/chaos   {"mode": "rate_limit", "count": 3}
```

| mode | effect |
|---|---|
| `rate_limit` | The next `count` requests (any endpoint) return 429 with `Retry-After: 2`. |
| `refund_commit_then_503` | The next `count` `POST /refunds` are **processed** but the response is a 503 `upstream_timeout`. |
| `latency_spike` | The next `count` requests are delayed by 5 seconds. |
| `clear` | Disarms all chaos. |

```
GET  /_admin/chaos     current armed modes
GET  /_admin/ledger    every refund and plan change created since the last reset, plus the
                       last 200 requests (method, path, status, idempotency key)
POST /_admin/reset     restore fixtures and clear the ledger
```

The ledger is the source of truth for "what actually happened". Your agent must not call
`/_admin` routes; they are for you and for the evaluator.

## Fixture customers

The fixtures contain 14 customers. Use `GET /customers?email=` to look them up. Explore
them; they are not all alike.
