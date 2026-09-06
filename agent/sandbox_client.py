import time
import uuid
from typing import Dict, Any, List, Optional, Callable
import httpx

class SandboxError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(f"[{code}] {message} (HTTP {status_code})")
        self.code = code
        self.message = message
        self.status_code = status_code

class SandboxClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8787", trace: bool = False, trace_callback: Optional[Callable[[str, Dict[str, Any], Any], None]] = None):
        self.base_url = base_url.rstrip("/")
        self.trace = trace
        self.trace_callback = trace_callback
        self.sandbox_now: Optional[str] = None
        self.client = httpx.Client(timeout=12.0)

    def _log_trace(self, tool_name: str, args: Dict[str, Any], result: Any):
        if self.trace:
            print(f"\n[TRACE] >>> Tool: {tool_name} | Args: {args}")
            print(f"[TRACE] <<< Result: {result}")
        if self.trace_callback:
            self.trace_callback(tool_name, args, result)

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, max_retries: int = 3) -> httpx.Response:
        url = f"{self.base_url}{path}"
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        for attempt in range(max_retries):
            try:
                resp = self.client.request(method, url, json=json, headers=req_headers)
                
                # Update sandbox clock from header
                if "x-sandbox-now" in resp.headers:
                    self.sandbox_now = resp.headers["x-sandbox-now"]

                # Handle Rate Limiting (429)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("retry-after", "2"))
                    if self.trace:
                        print(f"[TRACE] 429 Rate Limited. Sleeping for {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                return resp
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt == max_retries - 1:
                    raise SandboxError("network_error", f"Failed to connect to billing sandbox: {e}", 503)
                time.sleep(1.0)

        raise SandboxError("rate_limit_exceeded", "Rate limit retries exhausted", 429)

    def get_health(self) -> Dict[str, Any]:
        resp = self._request("GET", "/health")
        data = resp.json()
        self.sandbox_now = data.get("sandbox_now")
        return data

    def get_customers_by_email(self, email: str) -> List[Dict[str, Any]]:
        resp = self._request("GET", f"/customers?email={email}")
        if resp.status_code != 200:
            raise SandboxError("customer_lookup_failed", f"Failed to query customers: {resp.text}", resp.status_code)
        data = resp.json().get("data", [])
        self._log_trace("get_customers_by_email", {"email": email}, f"Found {len(data)} customers")
        return data

    def get_customer(self, customer_id: str) -> Dict[str, Any]:
        resp = self._request("GET", f"/customers/{customer_id}")
        if resp.status_code == 404:
            raise SandboxError("not_found", f"Customer {customer_id} not found", 404)
        data = resp.json()
        self._log_trace("get_customer", {"customer_id": customer_id}, data)
        return data

    def get_subscription(self, customer_id: str) -> Dict[str, Any]:
        resp = self._request("GET", f"/customers/{customer_id}/subscription")
        if resp.status_code == 404:
            raise SandboxError("not_found", f"Subscription for customer {customer_id} not found", 404)
        data = resp.json()
        self._log_trace("get_subscription", {"customer_id": customer_id}, data)
        return data

    def get_invoices(self, customer_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        resp = self._request("GET", f"/customers/{customer_id}/invoices?limit={min(limit, 10)}")
        if resp.status_code != 200:
            raise SandboxError("invoices_lookup_failed", f"Failed to get invoices: {resp.text}", resp.status_code)
        data = resp.json().get("data", [])
        self._log_trace("get_invoices", {"customer_id": customer_id}, f"{len(data)} invoices retrieved")
        return data

    def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        resp = self._request("GET", f"/invoices/{invoice_id}")
        if resp.status_code == 404:
            raise SandboxError("not_found", f"Invoice {invoice_id} not found", 404)
        data = resp.json()
        self._log_trace("get_invoice", {"invoice_id": invoice_id}, data)
        return data

    def preview_refund(self, invoice_id: str) -> Dict[str, Any]:
        resp = self._request("GET", f"/invoices/{invoice_id}/refund_preview")
        if resp.status_code != 200:
            err = resp.json().get("error", {})
            raise SandboxError(err.get("code", "refund_preview_failed"), err.get("message", resp.text), resp.status_code)
        data = resp.json()
        self._log_trace("preview_refund", {"invoice_id": invoice_id}, data)
        return data

    def create_refund(self, invoice_id: str, amount_minor: int, reason: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if not idempotency_key:
            idempotency_key = f"ref_{uuid.uuid4().hex[:12]}"

        headers = {"Idempotency-Key": idempotency_key}
        payload = {"invoice_id": invoice_id, "amount_minor": amount_minor, "reason": reason}

        resp = self._request("POST", "/refunds", json=payload, headers=headers)

        # Handling chaos mode: refund_commit_then_503
        if resp.status_code == 503:
            if self.trace:
                print(f"[TRACE] Received 503 upstream_timeout. Verifying if refund was committed via idempotency...")
            # Retry with the exact same idempotency key to recover the committed transaction
            check_resp = self._request("POST", "/refunds", json=payload, headers=headers)
            if check_resp.status_code in [200, 201]:
                data = check_resp.json()
                self._log_trace("create_refund [recovered from 503]", payload, data)
                return data

            # Also inspect the invoice state
            inv = self.get_invoice(invoice_id)
            if inv.get("refunded_minor", 0) >= amount_minor:
                data = {
                    "id": "re_recovered",
                    "invoice_id": invoice_id,
                    "amount_minor": amount_minor,
                    "status": "succeeded",
                    "idempotency_key": idempotency_key
                }
                self._log_trace("create_refund [verified via invoice]", payload, data)
                return data

            err = resp.json().get("error", {})
            raise SandboxError(err.get("code", "upstream_timeout"), err.get("message", "Payment gateway timed out"), 503)

        if resp.status_code not in [200, 201]:
            err = resp.json().get("error", {})
            raise SandboxError(err.get("code", "refund_failed"), err.get("message", resp.text), resp.status_code)

        data = resp.json()
        self._log_trace("create_refund", payload, data)
        return data

    def preview_plan_change(self, subscription_id: str, plan: str, billing_cycle: Optional[str] = None) -> Dict[str, Any]:
        params = f"?plan={plan}"
        if billing_cycle:
            params += f"&billing_cycle={billing_cycle}"
        resp = self._request("GET", f"/subscriptions/{subscription_id}/change_preview{params}")
        if resp.status_code == 403:
            raise SandboxError("plan_restriction", "Enterprise subscriptions cannot be changed self-serve.", 403)
        if resp.status_code != 200:
            err = resp.json().get("error", {})
            raise SandboxError(err.get("code", "change_preview_failed"), err.get("message", resp.text), resp.status_code)
        data = resp.json()
        self._log_trace("preview_plan_change", {"subscription_id": subscription_id, "plan": plan, "cycle": billing_cycle}, data)
        return data

    def change_subscription(self, subscription_id: str, plan: str, effective: str = "next_cycle", billing_cycle: Optional[str] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if not idempotency_key:
            idempotency_key = f"subchg_{uuid.uuid4().hex[:12]}"
        headers = {"Idempotency-Key": idempotency_key}
        payload = {"plan": plan, "effective": effective}
        if billing_cycle:
            payload["billing_cycle"] = billing_cycle

        resp = self._request("POST", f"/subscriptions/{subscription_id}/change", json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            err = resp.json().get("error", {})
            raise SandboxError(err.get("code", "subscription_change_failed"), err.get("message", resp.text), resp.status_code)
        data = resp.json()
        self._log_trace("change_subscription", payload, data)
        return data
