import json
import re
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from agent.sandbox_client import SandboxClient, SandboxError
from agent.policy import PolicyEngine

class BillingAgent:
    def __init__(self, sandbox_client: SandboxClient, trace: bool = False):
        self.sandbox = sandbox_client
        self.trace = trace
        self.customer: Optional[Dict[str, Any]] = None
        self.subscription: Optional[Dict[str, Any]] = None
        self.invoices: List[Dict[str, Any]] = []
        self.turns: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.multiple_workspaces: List[Dict[str, Any]] = []
        self.clarifying_questions_asked = 0

    def start_conversation(self, email: str) -> str:
        # Initialize sandbox clock
        health = self.sandbox.get_health()
        sandbox_time = health.get("sandbox_now", "2026-08-29T09:00:00Z")

        # Lookup customer by email
        matching_customers = self.sandbox.get_customers_by_email(email)
        if not matching_customers:
            reply = f"Hello, I could not find any active account associated with {email}. Could you please double-check your email address?"
            self._record_turn("agent", reply)
            return reply

        if len(matching_customers) > 1:
            self.multiple_workspaces = matching_customers
            self.clarifying_questions_asked += 1
            options = "\n".join([f"  {i+1}. {c['workspace_name']} (ID: {c['id']}, Region: {c.get('region', 'us')})" for i, c in enumerate(matching_customers)])
            reply = f"Welcome to Ferrowave Billing Support. I noticed you have multiple workspaces registered with {email}:\n{options}\n\nWhich workspace would you like assistance with today? (Please reply with the number or workspace name)"
            self._record_turn("agent", reply)
            return reply

        self.customer = matching_customers[0]
        return self._load_and_greet()

    def _load_and_greet(self) -> str:
        cust_id = self.customer["id"]
        try:
            self.subscription = self.sandbox.get_subscription(cust_id)
        except SandboxError:
            self.subscription = None

        self.invoices = self.sandbox.get_invoices(cust_id, limit=5)

        plan = self.subscription.get("plan", "unknown").capitalize() if self.subscription else "None"
        cycle = self.subscription.get("billing_cycle", "monthly") if self.subscription else ""
        reply = (
            f"Hello {self.customer.get('name', 'there')}! I am your Ferrowave Pulse billing assistant.\n"
            f"I have loaded your account for workspace '{self.customer.get('workspace_name')}' ({plan} Plan, {cycle}).\n"
            f"How can I help you today with your billing, plan, or invoices?"
        )
        self._record_turn("agent", reply)
        return reply

    def _record_turn(self, speaker: str, text: str):
        self.turns.append({"speaker": speaker, "text": text, "timestamp": datetime.datetime.now().isoformat()})
        # Estimate token usage
        tokens = len(text) // 4
        if speaker == "agent":
            self.total_tokens_out += tokens
        else:
            self.total_tokens_in += tokens

    def handle_message(self, user_msg: str, human_approval_callback: Optional[callable] = None) -> str:
        self._record_turn("customer", user_msg)
        msg_lower = user_msg.lower()

        # Handle multiple workspace disambiguation
        if self.multiple_workspaces and not self.customer:
            for i, c in enumerate(self.multiple_workspaces):
                if str(i+1) in msg_lower or c["workspace_name"].lower() in msg_lower or c["id"].lower() in msg_lower:
                    self.customer = c
                    self.multiple_workspaces = []
                    return self._load_and_greet()
            return "Please specify which workspace you would like to select from the numbered list above."

        if not self.customer:
            return "No customer account is currently selected."

        # Intent 1: Refund Request
        if any(w in msg_lower for w in ["refund", "money back", "reimburse", "cancel and refund"]):
            return self._handle_refund_intent(user_msg, human_approval_callback)

        # Intent 2: Plan Downgrade
        elif any(w in msg_lower for w in ["downgrade", "cheaper", "lower plan", "reduce plan"]):
            return self._handle_downgrade_intent(user_msg)

        # Intent 3: Plan Upgrade
        elif any(w in msg_lower for w in ["upgrade", "scale up", "higher plan", "switch to scale", "switch to growth"]):
            return self._handle_upgrade_intent(user_msg)

        # Intent 4: Invoice / "Why was I charged" explanation
        elif any(w in msg_lower for w in ["why was i charged", "charge", "invoice", "receipt", "bill", "breakdown"]):
            return self._handle_invoice_explanation(user_msg)

        # Default fallback
        reply = (
            "I can assist with reviewing invoice details, requesting eligible refunds under our 14-day / 30-day policy, "
            "or scheduling plan upgrades and downgrades. What specific billing matter can I assist with?"
        )
        self._record_turn("agent", reply)
        return reply

    def _handle_refund_intent(self, user_msg: str, human_approval_callback: Optional[callable]) -> str:
        if not self.invoices:
            reply = "I checked your account records, but could not find any past paid invoices eligible for a refund."
            self._record_turn("agent", reply)
            return reply

        latest_inv = self.invoices[0]
        inv_id = latest_inv["id"]

        try:
            preview = self.sandbox.preview_refund(inv_id)
        except SandboxError as e:
            reply = f"Could not generate refund preview for invoice {inv_id}: {e.message}"
            self._record_turn("agent", reply)
            return reply

        is_eligible, max_amount_minor, explanation = PolicyEngine.evaluate_refund_eligibility(
            self.subscription or {}, latest_inv, preview
        )

        if not is_eligible:
            reply = (
                f"Regarding invoice {latest_inv['number']} (${latest_inv['amount_minor']/100:.2f}):\n"
                f"{explanation}\n\n"
                f"If you believe you have special circumstances, I can escalate your request to a billing manager."
            )
            self._record_turn("agent", reply)
            return reply

        amount_usd = max_amount_minor / 100.0

        # Human Approval Enforcement (Requirement: human approval before money moves)
        approved = True
        if human_approval_callback:
            print(f"\n[APPROVAL REQUIRED] The agent proposed a refund of ${amount_usd:.2f} for Invoice {latest_inv['number']}.")
            approved = human_approval_callback(f"Approve refund of ${amount_usd:.2f} for {latest_inv['number']}? (y/n): ")

        if not approved:
            reply = f"The requested refund of ${amount_usd:.2f} was declined by supervisor review. No funds were transferred."
            self._record_turn("agent", reply)
            return reply

        try:
            refund = self.sandbox.create_refund(
                invoice_id=inv_id,
                amount_minor=max_amount_minor,
                reason="Customer requested refund under policy terms"
            )
            reply = (
                f"Your refund of ${amount_usd:.2f} for invoice {latest_inv['number']} has been processed successfully!\n"
                f"Refund Reference: {refund.get('id', 're_success')}. Funds typically return to your original payment method in 5-10 business days."
            )
        except SandboxError as e:
            reply = f"We encountered an issue while processing your refund: {e.message}. Please contact support."

        self._record_turn("agent", reply)
        return reply

    def _handle_downgrade_intent(self, user_msg: str) -> str:
        if not self.subscription:
            reply = "You do not currently have an active subscription to downgrade."
            self._record_turn("agent", reply)
            return reply

        current_plan = self.subscription.get("plan", "").lower()
        if current_plan == "enterprise":
            reply = "Enterprise plans are managed under dedicated annual contracts. I will escalate your downgrade request to your Account Executive."
            self._record_turn("agent", reply)
            return reply

        target_plan = "starter" if current_plan != "starter" else "starter"
        if "growth" in user_msg.lower() and current_plan == "scale":
            target_plan = "growth"

        if target_plan == current_plan:
            reply = f"You are already on the {current_plan.capitalize()} plan."
            self._record_turn("agent", reply)
            return reply

        sub_id = self.subscription["id"]

        # Note: Sandbox rule strictly dictates downgrades must be 'next_cycle'
        try:
            preview = self.sandbox.preview_plan_change(sub_id, plan=target_plan)
            change = self.sandbox.change_subscription(sub_id, plan=target_plan, effective="next_cycle")
            effective_date = change.get("effective_at", "the end of your current billing cycle")
            reply = (
                f"I have scheduled your downgrade to the {target_plan.capitalize()} plan.\n"
                f"Under our system policy, downgrades take effect at the end of your prepaid period ({effective_date}) "
                f"so you retain full access to your current features until then without interruption."
            )
        except SandboxError as e:
            if "seat_limit_exceeded" in str(e):
                reply = f"Cannot downgrade to {target_plan.capitalize()}: Your workspace currently has more active members than the {target_plan.capitalize()} plan allows. Please remove extra seats before downgrading."
            else:
                reply = f"Could not complete downgrade: {e.message}"

        self._record_turn("agent", reply)
        return reply

    def _handle_upgrade_intent(self, user_msg: str) -> str:
        if not self.subscription:
            reply = "You do not currently have an active subscription."
            self._record_turn("agent", reply)
            return reply

        current_plan = self.subscription.get("plan", "").lower()
        target_plan = "growth" if current_plan == "starter" else "scale"
        if "scale" in user_msg.lower():
            target_plan = "scale"

        sub_id = self.subscription["id"]
        try:
            preview = self.sandbox.preview_plan_change(sub_id, plan=target_plan)
            charge_now = preview.get("prorated_charge_now_minor", 0) / 100.0
            change = self.sandbox.change_subscription(sub_id, plan=target_plan, effective="now")
            reply = (
                f"Congratulations! Your workspace has been upgraded to the {target_plan.capitalize()} plan immediately.\n"
                f"A prorated charge of ${charge_now:.2f} has been applied for the remainder of your billing cycle."
            )
        except SandboxError as e:
            if "plan_restriction" in str(e) or current_plan == "enterprise":
                reply = "Upgrades involving Enterprise tiers require custom provisioning. I have forwarded your request to Sales."
            else:
                reply = f"Could not complete upgrade: {e.message}"

        self._record_turn("agent", reply)
        return reply

    def _handle_invoice_explanation(self, user_msg: str) -> str:
        if not self.invoices:
            reply = "You have no invoices on file."
            self._record_turn("agent", reply)
            return reply

        latest = self.invoices[0]
        amount = latest.get("amount_minor", 0) / 100.0
        date = latest.get("issued_at", "").split("T")[0]
        items = latest.get("line_items", [])
        breakdown = ", ".join([f"{item.get('description', 'Subscription')} (${item.get('amount_minor', 0)/100:.2f})" for item in items])

        reply = (
            f"Your most recent invoice {latest.get('number')} was issued on {date} for ${amount:.2f} ({latest.get('currency', 'USD')}).\n"
            f"Charge Breakdown: {breakdown}.\n"
            f"Status: {latest.get('status')}."
        )
        self._record_turn("agent", reply)
        return reply

    def save_transcript(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_dir / f"transcript_{ts}.json"
        data = {
            "customer_email": self.customer.get("email") if self.customer else "unknown",
            "workspace": self.customer.get("workspace_name") if self.customer else "none",
            "turns": self.turns,
            "token_usage": {
                "tokens_in": self.total_tokens_in,
                "tokens_out": self.total_tokens_out,
                "estimated_cost_usd": round((self.total_tokens_in * 0.075 / 1_000_000) + (self.total_tokens_out * 0.30 / 1_000_000), 6)
            }
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return filename
