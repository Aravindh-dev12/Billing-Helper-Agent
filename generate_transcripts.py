import sys
import time
import subprocess
from pathlib import Path
import httpx
from agent.sandbox_client import SandboxClient
from agent.orchestrator import BillingAgent

SANDBOX_URL = "http://127.0.0.1:8787"
server_proc = None

# Ensure server is running
try:
    r = httpx.get(f"{SANDBOX_URL}/health", timeout=1.0)
    if r.status_code != 200:
        raise Exception("Not ready")
except Exception:
    server_script = Path(__file__).resolve().parent / "sandbox" / "server.py"
    server_proc = subprocess.Popen([sys.executable, str(server_script), "--port", "8787"])
    time.sleep(1.5)

client = SandboxClient(base_url=SANDBOX_URL)
out_dir = Path(__file__).resolve().parent / "transcripts"
out_dir.mkdir(parents=True, exist_ok=True)

scenarios = [
    (
        "maya.chen@lumenbooks.example",
        ["I noticed a new subscription charge of $99 and would like a full refund please.", "Thank you, that was very helpful."],
        True
    ),
    (
        "daniel@brightcart.example",
        ["Why was I charged this month? Can I request a refund for this renewal?", "Alright, I understand. Please keep my cancellation scheduled for next cycle."],
        False
    ),
    (
        "priya@tolland.example",
        ["I would like to cancel my annual Starter plan and get a refund for my unused days.", "Yes, please proceed with the prorated refund."],
        True
    ),
    (
        "sam.okafor@northfield.example",
        ["1", "Could you give me a breakdown of what was billed on my recent invoice?", "Thanks! Everything looks clear."],
        False
    ),
    (
        "ahmed@meridian-systems.example",
        ["We need to downgrade our Enterprise account to a standard Starter plan.", "Understood, please have our account executive contact me."],
        False
    )
]

try:
    for idx, (email, msgs, auto_approve) in enumerate(scenarios, 1):
        # Reset fixtures before each scenario
        httpx.post(f"{SANDBOX_URL}/_admin/reset", timeout=5.0)
        
        agent = BillingAgent(client)
        agent.start_conversation(email)
        for m in msgs:
            agent.handle_message(m, human_approval_callback=lambda x: auto_approve)
        
        # Format filename cleanly
        ts = time.strftime("%Y%m%d_%H%M%S")
        filepath = out_dir / f"transcript_0{idx}_{email.split('@')[0]}_{ts}.json"
        
        data = {
            "conversation_id": f"conv_0{idx}",
            "customer_email": email,
            "turns": agent.turns,
            "token_usage": {
                "tokens_in": agent.total_tokens_in,
                "tokens_out": agent.total_tokens_out,
                "estimated_cost_usd": round((agent.total_tokens_in * 0.075 / 1_000_000) + (agent.total_tokens_out * 0.30 / 1_000_000), 6)
            }
        }
        import json
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[{idx}/5] Saved {filepath.name} (Spend: ${data['token_usage']['estimated_cost_usd']:.6f})")

finally:
    if server_proc:
        server_proc.terminate()
        server_proc.wait()
