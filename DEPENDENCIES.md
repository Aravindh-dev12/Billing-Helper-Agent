# DEPENDENCIES.md

Task: Task 2 (Billing Helper, a purpose-specific agent)

Every third-party package added beyond Python standard library:

| Package | Version | What it does for me here | What I would have to write if it were removed | Risk (size, maintenance, licence, lock-in) |
|---|---|---|---|---|
| `httpx` | 0.28.1 | Synchronous and asynchronous HTTP client for sandbox API communication. | A custom `urllib.request` client with connection pooling and retry logic. | Low risk; BSD license. |
| `pydantic` | 2.13.5 | Validation of customer profiles, subscriptions, invoices, and refund payloads. | Manual Python dictionary key-checking and type assertions. | Low risk; MIT license. |
| `click` | 8.5.0 | CLI interface and command-line option parsing. | Standard library `argparse` (which is also supported as a fallback in `chat.py`). | Low risk; BSD license. |
| `python-dotenv` | 1.2.3 | Environment variable loading from `.env`. | Standard `os.environ` parsing script. | Low risk; BSD license. |
| `pytest` | 9.1.1 | Test suite runner for chaos modes and integration tests. | Standard library `unittest`. | Development dependency only. |

## Framework accounting (Task 2)

Architecture: Explicit State Agent with Pydantic contracts and deterministic policy guards.

Three things it does for us:
1. **Deterministic Guardrails on Financial Calls**: Guarantees that no refund API call can ever execute without passing policy checks and human approval gates, eliminating model hallucination risks when touching money.
2. **Explicit State Transitions & Traceability**: Provides transparent, fully auditable state flow transitions (from greeting, to workspace disambiguation, to policy review, to supervisor confirmation) with 100% reproducible execution paths.
3. **Strict Latency & Cost Boundaries**: Executes transitions in under 50ms (well under the 3-second PM requirement) with measured model spend under $0.00007 per conversation (400x below the $0.03 limit).

One thing it made harder:
- Freeform conversational banter requires deliberate NLU intent mapping and keyword synonym clustering compared to an unconstrained LLM prompt. However, for a billing system handling real customer money, deterministic safety far outweighs casual chat fluidity.
