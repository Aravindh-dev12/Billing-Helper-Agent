import sys
import time
import subprocess
import pytest
import httpx
from pathlib import Path
from agent.sandbox_client import SandboxClient, SandboxError
from agent.orchestrator import BillingAgent
from agent.policy import PolicyEngine

SANDBOX_PORT = 8787
SANDBOX_URL = f"http://127.0.0.1:{SANDBOX_PORT}"

@pytest.fixture(scope="session", autouse=True)
def start_sandbox():
    # Check if sandbox is already running
    try:
        r = httpx.get(f"{SANDBOX_URL}/health", timeout=1.0)
        if r.status_code == 200:
            yield
            return
    except Exception:
        pass

    # Start sandbox process
    server_script = Path(__file__).resolve().parent.parent / "sandbox" / "server.py"
    proc = subprocess.Popen([sys.executable, str(server_script), "--port", str(SANDBOX_PORT)])
    time.sleep(1.5)
    yield
    proc.terminate()
    proc.wait()

@pytest.fixture(autouse=True)
def reset_sandbox():
    # Reset fixtures and disarm chaos before each test
    httpx.post(f"{SANDBOX_URL}/_admin/reset", timeout=5.0)
    httpx.post(f"{SANDBOX_URL}/_admin/chaos", json={"mode": "clear"}, timeout=5.0)

def test_multi_customer_email_collision():
    """Test when one email maps to multiple workspaces (e.g. sam.okafor@northfield.example)."""
    client = SandboxClient(base_url=SANDBOX_URL)
    agent = BillingAgent(client)

    reply = agent.start_conversation("sam.okafor@northfield.example")
    assert "multiple workspaces" in reply.lower()
    assert "Northfield Pilot" in reply
    assert "Northfield Main" in reply

    # Customer disambiguates
    sel_reply = agent.handle_message("1")
    assert "Northfield Pilot" in sel_reply
    assert agent.customer is not None
    assert agent.customer["id"] == "cust_0003"

def test_rate_limit_chaos_mode():
    """Test 429 rate limit backoff and retry."""
    # Arm rate limit for 2 requests
    httpx.post(f"{SANDBOX_URL}/_admin/chaos", json={"mode": "rate_limit", "count": 2})

    client = SandboxClient(base_url=SANDBOX_URL)
    # The client should transparently sleep for Retry-After and succeed
    custs = client.get_customers_by_email("daniel@brightcart.example")
    assert len(custs) == 1
    assert custs[0]["name"] == "Daniel Okoro"

def test_refund_commit_then_503_chaos_mode():
    """Test chaos mode: refund committed in database but returns 503 upstream timeout."""
    # Find an eligible invoice for cust_0007 (annual starter, inside window)
    client = SandboxClient(base_url=SANDBOX_URL)
    invs = client.get_invoices("cust_0007")
    target_inv = invs[0]

    # Arm chaos mode for 1 commit
    httpx.post(f"{SANDBOX_URL}/_admin/chaos", json={"mode": "refund_commit_then_503", "count": 1})

    # Execute refund with idempotency
    refund = client.create_refund(
        invoice_id=target_inv["id"],
        amount_minor=1000,
        reason="Test 503 recovery",
        idempotency_key="idemp_test_503"
    )
    assert refund["status"] == "succeeded"

    # Verify sandbox ledger recorded exactly ONE refund (no duplicates)
    ledger = httpx.get(f"{SANDBOX_URL}/_admin/ledger").json()
    refunds_for_inv = [r for r in ledger["refunds"] if r["invoice_id"] == target_inv["id"]]
    assert len(refunds_for_inv) == 1

def test_latency_spike_chaos_mode():
    """Test handling 5-second latency spike without crashing."""
    httpx.post(f"{SANDBOX_URL}/_admin/chaos", json={"mode": "latency_spike", "count": 1})
    client = SandboxClient(base_url=SANDBOX_URL)
    start = time.perf_counter()
    custs = client.get_customers_by_email("daniel@brightcart.example")
    elapsed = time.perf_counter() - start
    assert elapsed >= 4.5
    assert len(custs) == 1

def test_enterprise_plan_restriction():
    """Test enterprise subscription cannot be changed self-serve and must escalate."""
    client = SandboxClient(base_url=SANDBOX_URL)
    agent = BillingAgent(client)
    agent.start_conversation("ahmed@meridian-systems.example")
    assert agent.customer["id"] == "cust_0009"

    reply = agent.handle_message("I want to downgrade to Starter")
    assert "enterprise" in reply.lower()
    assert "escalate" in reply.lower() or "account executive" in reply.lower()

def test_monthly_renewal_non_refundable():
    """Test Refund Policy Section 1.2: monthly renewal charges are non-refundable."""
    client = SandboxClient(base_url=SANDBOX_URL)
    # cust_0001 (Daniel Okoro) has monthly renewal invoice
    agent = BillingAgent(client)
    agent.start_conversation("daniel@brightcart.example")

    reply = agent.handle_message("Can I get a refund on my latest renewal charge?", human_approval_callback=lambda x: True)
    assert "non-refundable" in reply.lower() or "ineligible" in reply.lower() or "renewal" in reply.lower()
