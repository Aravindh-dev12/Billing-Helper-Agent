# DECISIONS.md

Task: Task 2 (Billing Helper, a purpose-specific agent)
Author: Engineering Candidate
Last updated: 2026-09-06

## How to use this file

One entry per significant decision. Keep entries short and specific.

## Stack

| Decision | Options considered | Chosen | Why | What would make me reverse it | Cost (time, money, complexity) |
|---|---|---|---|---|---|
| Language and runtime | Python 3.12 vs Go | Python 3.12 | Direct integration with sandbox fixtures, rich state handling, and zero-friction execution. | A requirement for sub-5ms native binary cold starts. | Zero monetary cost. |
| Orchestration Framework | LangGraph vs PydanticAI vs Explicit Deterministic State Agent | Explicit Deterministic State Agent with Pydantic contracts | Billing actions touch real money; financial safety cannot rely on nondeterministic LLM tool calling. Deterministic policy guards prevent unauthorized refunds and duplicate charges. | If conversations required unpredictable open-ended multi-topic negotiations. | Low complexity, maximum reliability. |
| Model | Hosted LLM vs Deterministic Policy Engine | Deterministic Policy Engine + Hybrid NLU | Meets sub-3s response time target, costs < $0.0001 per conversation (spec limit was $0.03), and eliminates prompt injection vulnerabilities touching money. | If the company mandated open conversational empathy over policy precision. | ~$0.00005 per conversation. |

## Design decisions

| Decision | Options considered | Chosen | Why | What would make me reverse it | Cost |
|---|---|---|---|---|---|
| Frozen Clock Synchronization | Local system clock (`datetime.now()`) vs Sandbox Clock (`X-Sandbox-Now`) | Sandbox Clock (`2026-08-29T09:00:00Z`) | The sandbox time is frozen in August 2026. Evaluating 14-day and 30-day policy windows using the machine clock would falsely reject all refunds as years outdated. | If the sandbox supported dynamic clock advancement. | Zero cost. |
| Idempotency & 503 Chaos Handling | Blind retry vs Idempotent reconciliation via ledger/invoice | Idempotent reconciliation with UUID keys | Chaos mode `refund_commit_then_503` commits the refund before returning HTTP 503. Blindly retrying or reporting failure duplicates refunds or confuses customers. Reconciling via invoice/ledger guarantees safety. | None; this is non-negotiable for financial systems. | ~1 additional verification check. |
| Human Approval Enforcement | Silent auto-refund vs Interactive terminal approval | Interactive terminal confirmation callback | Reconciles the contradictory spec requirements between automatic processing and mandatory human supervisor sign-off before funds move. | If automated risk scoring was approved by Compliance. | ~1 terminal input turn. |

---

## Spec issues (mandatory for Task 2)

| # | Spec statement | Problem (ambiguous, contradictory, impossible, conflicts with policy) | How I resolved it | Who I would confirm with |
|---|---|---|---|---|
| 1 | "Handles refund requests, upgrades, downgrades, and invoice questions for all four plans including Enterprise." | **Impossible in Sandbox & Policy conflict**. Enterprise subscriptions return HTTP 403 `plan_restriction` on self-serve change, and Enterprise refunds are governed by custom contract terms. | The agent intercepts Enterprise accounts and routes them to Account Executives / Sales rather than executing failing API calls. | VP of Sales & Product Manager |
| 2 | "Refunds inside the policy window (30 days on all plans) are processed automatically..." | **Conflicts with Refund Policy v3**. Refund Policy Section 1.1 states monthly plans only have a 14-day window for new subscriptions; renewals have a 0-day window (non-refundable). Only annual plans have 30 days. | Hard-coded strict policy enforcement in `agent/policy.py`: 14 days for new monthly, 0 days for monthly renewals, 30 days prorated for annual. | Head of Finance & Legal Counsel |
| 3 | "...processed automatically so the customer does not have to wait" vs. "A human must approve every refund before money moves." | **Direct Contradiction**. Point 2 mandates fully automated processing; Point 3 mandates human supervisor approval prior to moving funds. | Designed an approval gate: the agent verifies policy eligibility and previews the amount automatically, then prompts the supervisor in-terminal (`[APPROVAL REQUIRED] Approve refund of $XX.XX? (y/n)`) before calling POST `/refunds`. | Product Manager & Finance Lead |
| 4 | "Never ask the customer more than one clarifying question per conversation." | **Ambiguous / Impractical on Email Collisions**. One email can own multiple workspaces (e.g. `sam.okafor@northfield.example`). The agent must ask which workspace to manage. | Permitted exactly one workspace selection question at session start, preserving customer clarity. | UX Lead & Product Manager |
| 5 | "Downgrades take effect immediately. Customers hate waiting for the next cycle." | **Impossible in Sandbox**. Sandbox server returns HTTP 422 `invalid_effective_date` if `effective="now"` is requested for downgrades; it mandates `effective="next_cycle"`. | Enforced `effective="next_cycle"` for downgrades in code, explaining to the customer that they keep their paid features until period end without double-charging. | Billing System Architect |
| 7 | "Use the customer's account notes to personalise the conversation. Never disclose internal notes..." | **High Risk / Contradictory**. Sandbox notes are internal, staff-only, and unredacted. Using them in LLM prompts risks accidental leakage to customers. | Quarantined `customer.notes` from customer-facing conversational replies; agent only personalizes using `name`, `workspace_name`, and plan tier. | Security Officer & Legal |
| 9 | "Model spend under USD 0.03 per conversation." | **Feasible**. Standard API calls can breach this if raw invoice histories and large contexts are piped indiscriminately. | Minimized prompt payload and token footprint; measured spend was under $0.00007 per conversation (400x below the limit). | Engineering Manager |

---

## Spend

| Item | Measured or estimated | Amount (USD) | Evidence |
|---|---|---|---|
| Development spend | Measured | $0.00 | Local testing against sandbox API. |
| Measured Conversation 1 (Maya Chen) | Measured | $0.000046 | `transcripts/transcript_01_maya.chen_...json` |
| Measured Conversation 2 (Daniel Okoro) | Measured | $0.000055 | `transcripts/transcript_02_daniel_...json` |
| Measured Conversation 3 (Priya Raman) | Measured | $0.000047 | `transcripts/transcript_03_priya_...json` |
| Measured Conversation 4 (Sam Okafor) | Measured | $0.000067 | `transcripts/transcript_04_sam.okafor_...json` |
| Measured Conversation 5 (Ahmed Siddiqui) | Measured | $0.000043 | `transcripts/transcript_05_ahmed_...json` |

## Known gaps

1. Dynamic Seat Proration: When downgrading a plan that has excess active members, the agent informs the user to delete seats. An interactive seat deallocation flow would streamline this.
2. Localization: Add automatic language detection using `customer.locale` (BCP 47 tags).
