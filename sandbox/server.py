#!/usr/bin/env python3
"""
Ferrowave Billing Sandbox
=========================
A local stand-in for the Ferrowave Pulse billing system, for the Billing Helper agent task.
Zero dependencies: Python 3.9+ standard library only.

Run:      python3 server.py            (listens on http://127.0.0.1:8787)
Options:  python3 server.py --port 9000 --fixtures fixtures.json --seed 7

Read API_REFERENCE.md before integrating. The sandbox reproduces behaviours of the real
billing system, including its failure modes. Reading this source is allowed and encouraged.
"""
import argparse, copy, json, os, random, threading, time, uuid
from collections import deque
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.RLock()
PAGE_SIZE = 10
RATE_LIMIT_MAX = 40          # requests
RATE_LIMIT_WINDOW = 10.0     # seconds
PLAN_RANK = {"starter": 1, "growth": 2, "scale": 3, "enterprise": 4}
PLAN_PRICE = {"starter": (2900, 29000), "growth": (9900, 99000), "scale": (29900, 299000)}
SEATS_INCLUDED = {"starter": 3, "growth": 10, "scale": 25, "enterprise": 100}


class State:
    def __init__(self, fixtures_path, seed):
        self.fixtures_path = fixtures_path
        self.rng = random.Random(seed)
        self.reset()

    def reset(self):
        with open(self.fixtures_path, encoding="utf-8") as f:
            fx = json.load(f)
        self.now = datetime.strptime(fx["sandbox_now"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.customers = {c["id"]: c for c in copy.deepcopy(fx["customers"])}
        self.subscriptions = {s["id"]: s for s in copy.deepcopy(fx["subscriptions"])}
        self.invoices = {i["id"]: i for i in copy.deepcopy(fx["invoices"])}
        self.refunds = []
        self.plan_changes = []
        self.idempotency = {}      # key -> (request_fingerprint, response_body)
        self.chaos = {}            # mode -> remaining count
        self.request_log = deque(maxlen=200)
        self.rate_window = deque()
        self.started_at = time.time()


STATE = None


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def error(code, message, status):
    return status, {"error": {"code": code, "message": message}}


def consume_chaos(mode):
    """Return True if a chaos mode is armed, decrementing its counter."""
    with LOCK:
        n = STATE.chaos.get(mode, 0)
        if n > 0:
            STATE.chaos[mode] = n - 1
            if STATE.chaos[mode] == 0:
                del STATE.chaos[mode]
            return True
        return False


def proration(invoice, sub):
    """Prorated refund of an annual or monthly invoice as of the sandbox clock."""
    start = parse_iso(invoice["period_start"])
    end = parse_iso(invoice["period_end"])
    total_days = max((end - start).days, 1)
    used_days = max(min((STATE.now - start).days, total_days), 0)
    unused = total_days - used_days
    return int(round(invoice["amount_minor"] * unused / total_days)), used_days, total_days


class Handler(BaseHTTPRequestHandler):
    server_version = "FerrowaveSandbox/1.3"

    def log_message(self, fmt, *args):  # quieter default logging
        pass

    # ---------- plumbing ----------
    def _send(self, status, body, extra_headers=None):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Sandbox-Now", iso(STATE.now))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _pre(self, method, path):
        """Rate limiting, chaos, latency. Returns (status, body, headers) to short-circuit or None."""
        if path.startswith("/_admin") or path == "/health":
            return None
        with LOCK:
            now = time.time()
            while STATE.rate_window and now - STATE.rate_window[0] > RATE_LIMIT_WINDOW:
                STATE.rate_window.popleft()
            if len(STATE.rate_window) >= RATE_LIMIT_MAX:
                return (429, {"error": {"code": "rate_limited", "message": "Too many requests. Respect Retry-After."}}, {"Retry-After": "3"})
            STATE.rate_window.append(now)
        if consume_chaos("rate_limit"):
            return (429, {"error": {"code": "rate_limited", "message": "Too many requests (sandbox chaos). Respect Retry-After."}}, {"Retry-After": "2"})
        if consume_chaos("latency_spike"):
            time.sleep(5.0)
        else:
            time.sleep(STATE.rng.uniform(0.10, 0.35))
        return None

    def _record(self, method, path, status, key=None):
        with LOCK:
            STATE.request_log.append({"ts": round(time.time() - STATE.started_at, 3), "method": method, "path": path,
                                      "status": status, "idempotency_key": key})

    # ---------- routing ----------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        short = self._pre(method, path)
        if short:
            status, body, headers = short
            self._record(method, path, status)
            return self._send(status, body, headers)
        body = self._body() if method == "POST" else {}
        if body is None:
            self._record(method, path, 400)
            return self._send(400, {"error": {"code": "invalid_json", "message": "Body must be valid JSON."}})
        key = self.headers.get("Idempotency-Key")
        try:
            status, resp, headers = self.dispatch(method, path, qs, body, key)
        except Exception as e:  # pragma: no cover
            status, resp, headers = 500, {"error": {"code": "internal", "message": str(e)}}, {}
        self._record(method, path, status, key)
        self._send(status, resp, headers)

    def dispatch(self, method, path, qs, body, key):
        parts = [p for p in path.split("/") if p]
        with LOCK:
            # health
            if path == "/health":
                return 200, {"ok": True, "sandbox_now": iso(STATE.now), "version": self.server_version,
                             "chaos_armed": dict(STATE.chaos)}, {}
            # admin
            if parts and parts[0] == "_admin":
                return self.admin(method, parts, body)
            # customers
            if method == "GET" and path == "/customers":
                email = (qs.get("email") or "").strip().lower()
                if not email:
                    return error("validation_error", "email query parameter is required", 400) + ({},)
                data = [c for c in STATE.customers.values() if c["email"].lower() == email]
                return 200, {"data": data, "count": len(data)}, {}
            if method == "GET" and len(parts) == 2 and parts[0] == "customers":
                c = STATE.customers.get(parts[1])
                if not c:
                    return error("not_found", "customer not found", 404) + ({},)
                return 200, c, {}
            if method == "GET" and len(parts) == 3 and parts[0] == "customers" and parts[2] == "subscription":
                c = STATE.customers.get(parts[1])
                if not c:
                    return error("not_found", "customer not found", 404) + ({},)
                return 200, STATE.subscriptions[c["subscription_id"]], {}
            if method == "GET" and len(parts) == 3 and parts[0] == "customers" and parts[2] == "invoices":
                c = STATE.customers.get(parts[1])
                if not c:
                    return error("not_found", "customer not found", 404) + ({},)
                invs = sorted([i for i in STATE.invoices.values() if i["customer_id"] == c["id"]],
                              key=lambda i: i["issued_at"], reverse=True)
                limit = min(int(qs.get("limit", PAGE_SIZE)), PAGE_SIZE)
                offset = 0
                if qs.get("cursor"):
                    try:
                        offset = int(qs["cursor"].split("_")[1])
                    except Exception:
                        return error("validation_error", "invalid cursor", 400) + ({},)
                page = invs[offset: offset + limit]
                nxt = f"cur_{offset + limit}" if offset + limit < len(invs) else None
                return 200, {"data": page, "next_cursor": nxt}, {}
            # invoices
            if method == "GET" and len(parts) == 2 and parts[0] == "invoices":
                i = STATE.invoices.get(parts[1])
                if not i:
                    return error("not_found", "invoice not found", 404) + ({},)
                return 200, i, {}
            if method == "GET" and len(parts) == 3 and parts[0] == "invoices" and parts[2] == "refund_preview":
                i = STATE.invoices.get(parts[1])
                if not i:
                    return error("not_found", "invoice not found", 404) + ({},)
                sub = STATE.subscriptions[i["subscription_id"]]
                pro, used, total = proration(i, sub)
                remaining = i["amount_minor"] - i["refunded_minor"]
                return 200, {"invoice_id": i["id"], "currency": i["currency"], "amount_minor": i["amount_minor"],
                             "refunded_minor": i["refunded_minor"], "refundable_minor": remaining,
                             "prorated_unused_minor": min(pro, remaining), "days_used": used, "days_in_period": total,
                             "days_since_issued": (STATE.now - parse_iso(i["issued_at"])).days,
                             "note": "The sandbox does not apply the Refund Policy. It only computes amounts."}, {}
            # refunds
            if method == "POST" and path == "/refunds":
                return self.create_refund(body, key)
            if method == "GET" and len(parts) == 2 and parts[0] == "refunds":
                for r in STATE.refunds:
                    if r["id"] == parts[1]:
                        return 200, r, {}
                return error("not_found", "refund not found", 404) + ({},)
            # subscriptions
            if method == "GET" and len(parts) == 2 and parts[0] == "subscriptions":
                s = STATE.subscriptions.get(parts[1])
                if not s:
                    return error("not_found", "subscription not found", 404) + ({},)
                return 200, s, {}
            if method == "GET" and len(parts) == 3 and parts[0] == "subscriptions" and parts[2] == "change_preview":
                return self.change_preview(parts[1], qs)
            if method == "POST" and len(parts) == 3 and parts[0] == "subscriptions" and parts[2] == "change":
                return self.change_plan(parts[1], body, key)
            return error("not_found", f"no route for {method} {path}", 404) + ({},)

    # ---------- refunds ----------
    def create_refund(self, body, key):
        fingerprint = "refund:" + json.dumps(body, sort_keys=True)
        if key:
            prev = STATE.idempotency.get(key)
            if prev:
                if prev[0] != fingerprint:
                    return error("idempotency_mismatch", "Idempotency-Key was already used with a different request body.", 422) + ({},)
                return 200, prev[1], {"Idempotent-Replayed": "true"}
        inv_id = body.get("invoice_id")
        amount = body.get("amount_minor")
        reason = (body.get("reason") or "").strip()
        inv = STATE.invoices.get(inv_id or "")
        if not inv:
            return error("not_found", "invoice not found", 404) + ({},)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            return error("validation_error", "amount_minor must be a positive integer in minor units (cents).", 400) + ({},)
        if not reason:
            return error("validation_error", "reason is required", 400) + ({},)
        if inv["status"] not in ("paid", "partially_refunded"):
            return error("invalid_state", f"invoice status is {inv['status']}; only paid invoices can be refunded", 409) + ({},)
        remaining = inv["amount_minor"] - inv["refunded_minor"]
        if amount > remaining:
            return error("amount_exceeds_refundable", f"amount_minor {amount} exceeds refundable {remaining}", 409) + ({},)
        # simulate processing time
        time.sleep(STATE.rng.uniform(0.6, 0.9))
        refund = {"id": f"re_{uuid.uuid4().hex[:10]}", "invoice_id": inv["id"], "customer_id": inv["customer_id"],
                  "amount_minor": amount, "currency": inv["currency"], "reason": reason, "status": "succeeded",
                  "created_at": iso(STATE.now), "idempotency_key": key}
        inv["refunded_minor"] += amount
        inv["status"] = "refunded" if inv["refunded_minor"] >= inv["amount_minor"] else "partially_refunded"
        STATE.refunds.append(refund)
        if key:
            STATE.idempotency[key] = (fingerprint, refund)
        if consume_chaos("refund_commit_then_503"):
            # The refund has been committed, but the client only sees a 503.
            return 503, {"error": {"code": "upstream_timeout", "message": "The payment provider did not respond in time. The request may or may not have been processed."}}, {}
        return 201, refund, {}

    # ---------- subscriptions ----------
    def change_preview(self, sub_id, qs):
        sub = STATE.subscriptions.get(sub_id)
        if not sub:
            return error("not_found", "subscription not found", 404) + ({},)
        plan = (qs.get("plan") or "").lower()
        cycle = (qs.get("billing_cycle") or sub["billing_cycle"]).lower()
        if plan not in PLAN_PRICE:
            return error("validation_error", "plan must be one of starter, growth, scale", 400) + ({},)
        if sub["plan"] == "enterprise":
            return error("plan_restriction", "Enterprise subscriptions are changed by your account manager, not through self-serve.", 403) + ({},)
        if plan == sub["plan"] and cycle == sub["billing_cycle"]:
            direction = "same"
        elif plan == sub["plan"]:
            direction = "cycle_change"
        else:
            direction = "upgrade" if PLAN_RANK[plan] > PLAN_RANK[sub["plan"]] else "downgrade"
        new_price = PLAN_PRICE[plan][0 if cycle == "monthly" else 1]
        ps, pe = parse_iso(sub["current_period_start"]), parse_iso(sub["current_period_end"])
        total = max((pe - ps).days, 1)
        remaining = max((pe - STATE.now).days, 0)
        credit = int(round((sub["price_minor"] or 0) * remaining / total))
        charge = int(round(new_price * remaining / total)) if cycle == sub["billing_cycle"] else new_price
        out = {"subscription_id": sub_id, "current_plan": sub["plan"], "new_plan": plan, "billing_cycle": cycle,
               "direction": direction, "new_price_minor": new_price, "currency": "USD",
               "seats_included_on_new_plan": SEATS_INCLUDED[plan], "seats_used": sub["seats_used"]}
        if direction in ("upgrade", "cycle_change"):
            out.update({"effective_options": ["now", "next_cycle"], "prorated_charge_now_minor": max(charge - credit, 0),
                        "unused_credit_minor": credit})
        elif direction == "downgrade":
            out.update({"effective_options": ["next_cycle"], "prorated_charge_now_minor": 0, "unused_credit_minor": 0,
                        "effective_at": sub["current_period_end"],
                        "seat_limit_ok": sub["seats_used"] <= SEATS_INCLUDED[plan]})
        else:
            out.update({"effective_options": [], "message": "already on this plan"})
        return 200, out, {}

    def change_plan(self, sub_id, body, key):
        sub = STATE.subscriptions.get(sub_id)
        if not sub:
            return error("not_found", "subscription not found", 404) + ({},)
        fingerprint = "change:" + sub_id + ":" + json.dumps(body, sort_keys=True)
        if key:
            prev = STATE.idempotency.get(key)
            if prev:
                if prev[0] != fingerprint:
                    return error("idempotency_mismatch", "Idempotency-Key was already used with a different request body.", 422) + ({},)
                return 200, prev[1], {"Idempotent-Replayed": "true"}
        if sub["plan"] == "enterprise":
            return error("plan_restriction", "Enterprise subscriptions are changed by your account manager, not through self-serve.", 403) + ({},)
        plan = (body.get("plan") or "").lower()
        effective = (body.get("effective") or "").lower()
        cycle = (body.get("billing_cycle") or sub["billing_cycle"]).lower()
        if plan not in PLAN_PRICE:
            return error("validation_error", "plan must be one of starter, growth, scale", 400) + ({},)
        if effective not in ("now", "next_cycle"):
            return error("validation_error", "effective must be 'now' or 'next_cycle'", 400) + ({},)
        if plan == sub["plan"] and cycle == sub["billing_cycle"]:
            return error("invalid_state", "subscription is already on that plan and cycle", 409) + ({},)
        if plan == sub["plan"]:
            direction = "cycle_change"
        else:
            direction = "upgrade" if PLAN_RANK[plan] > PLAN_RANK[sub["plan"]] else "downgrade"
        if direction == "downgrade":
            if effective == "now":
                return error("invalid_effective_date", "Downgrades take effect at the next billing cycle. Use effective=next_cycle.", 422) + ({},)
            if sub["seats_used"] > SEATS_INCLUDED[plan]:
                return error("seat_limit_exceeded",
                             f"{plan} includes {SEATS_INCLUDED[plan]} seats but the workspace has {sub['seats_used']} members. Remove members before scheduling the downgrade.", 422) + ({},)
        change = {"id": f"chg_{uuid.uuid4().hex[:10]}", "subscription_id": sub_id, "from_plan": sub["plan"], "to_plan": plan,
                  "billing_cycle": cycle, "direction": direction, "effective": effective,
                  "effective_at": iso(STATE.now) if effective == "now" else sub["current_period_end"],
                  "created_at": iso(STATE.now), "idempotency_key": key}
        if effective == "now":
            sub["plan"] = plan
            sub["billing_cycle"] = cycle
            sub["price_minor"] = PLAN_PRICE[plan][0 if cycle == "monthly" else 1]
            sub["seats_included"] = SEATS_INCLUDED[plan]
        else:
            sub["pending_change"] = {"plan": plan, "billing_cycle": cycle, "effective_at": sub["current_period_end"]}
        STATE.plan_changes.append(change)
        if key:
            STATE.idempotency[key] = (fingerprint, change)
        return 201, change, {}

    # ---------- admin ----------
    def admin(self, method, parts, body):
        sub = parts[1] if len(parts) > 1 else ""
        if method == "POST" and sub == "reset":
            STATE.reset()
            return 200, {"ok": True, "message": "state restored from fixtures"}, {}
        if method == "POST" and sub == "chaos":
            mode = body.get("mode")
            count = int(body.get("count", 1))
            if mode == "clear":
                STATE.chaos = {}
                return 200, {"ok": True, "chaos_armed": {}}, {}
            if mode not in ("rate_limit", "refund_commit_then_503", "latency_spike"):
                return error("validation_error", "mode must be rate_limit, refund_commit_then_503, latency_spike, or clear", 400) + ({},)
            STATE.chaos[mode] = STATE.chaos.get(mode, 0) + count
            return 200, {"ok": True, "chaos_armed": dict(STATE.chaos)}, {}
        if method == "GET" and sub == "ledger":
            return 200, {"sandbox_now": iso(STATE.now), "refunds": STATE.refunds, "plan_changes": STATE.plan_changes,
                         "invoices_touched": [i for i in STATE.invoices.values() if i["refunded_minor"] > 0],
                         "pending_changes": {sid: s["pending_change"] for sid, s in STATE.subscriptions.items() if s.get("pending_change")},
                         "request_log": list(STATE.request_log)}, {}
        if method == "GET" and sub == "chaos":
            return 200, {"chaos_armed": dict(STATE.chaos)}, {}
        return error("not_found", "unknown admin route", 404) + ({},)


def main():
    global STATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fixtures", default=os.path.join(HERE, "fixtures.json"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    STATE = State(args.fixtures, args.seed)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Ferrowave billing sandbox listening on http://{args.host}:{args.port}  (sandbox clock frozen at {iso(STATE.now)})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
