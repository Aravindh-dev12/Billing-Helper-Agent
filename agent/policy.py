from typing import Dict, Any, Tuple, Optional

class PolicyEngine:
    @staticmethod
    def evaluate_refund_eligibility(
        subscription: Dict[str, Any],
        invoice: Dict[str, Any],
        refund_preview: Dict[str, Any]
    ) -> Tuple[bool, int, str]:
        """
        Evaluates refund eligibility strictly according to Ferrowave Refund Policy v3.
        Returns: (is_eligible, max_allowed_minor, explanation)
        """
        plan = subscription.get("plan", "").lower()
        billing_cycle = subscription.get("billing_cycle", "").lower()
        kind = invoice.get("kind", "")
        days_since_issued = refund_preview.get("days_since_issued", 999)
        refundable_minor = refund_preview.get("refundable_minor", 0)

        # 1. Enterprise Plan
        if plan == "enterprise":
            return False, 0, "Enterprise refunds are governed by custom contract terms and require escalation to your Account Executive."

        if refundable_minor <= 0:
            return False, 0, "This invoice has already been fully refunded."

        # 2. Monthly Plans (Starter, Growth, Scale)
        if billing_cycle == "monthly":
            if kind == "new_subscription":
                if days_since_issued <= 14:
                    return True, refundable_minor, f"Eligible for full refund under 14-day new monthly subscription policy ({days_since_issued} days since charge)."
                else:
                    return False, 0, f"Ineligible: monthly new subscription refund request exceeds the 14-day policy window ({days_since_issued} days elapsed)."
            elif kind == "renewal":
                return False, 0, "Ineligible: Under Section 1.2 of the Ferrowave Refund Policy, monthly renewal charges are non-refundable. You may cancel to prevent future charges."
            elif kind == "addon" or kind == "seat_change":
                return False, 0, "Additional seat and add-on charges are non-refundable unless removed within 24 hours."
            elif kind == "overage":
                return False, 0, "Response quota overage charges are non-refundable under Policy Section 1.3."

        # 3. Annual Plans (Starter, Growth, Scale)
        if billing_cycle == "annual":
            if days_since_issued <= 30:
                # Prorated unused portion
                prorated = refund_preview.get("prorated_unused_minor", refundable_minor)
                return True, min(prorated, refundable_minor), f"Eligible for prorated refund within the 30-day annual policy window ({days_since_issued} days elapsed)."
            else:
                return False, 0, f"Ineligible: annual subscription refund request exceeds the 30-day policy window ({days_since_issued} days elapsed)."

        return False, 0, "Charge is outside policy terms."
