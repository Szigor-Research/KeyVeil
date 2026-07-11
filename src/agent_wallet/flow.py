from __future__ import annotations

import time

from .approval import ApprovalGrant, ApprovalVerifier
from .audit_receipt import DecisionStatus, PaymentIntent, PaymentReceipt
from .budget_store import BudgetStore
from .policy_engine import PolicyEngine
from .session_scope import SessionScope


def evaluate_payment(
    scope: SessionScope,
    engine: PolicyEngine,
    intent: PaymentIntent,
    *,
    budget_store: BudgetStore | None,
    approval: ApprovalGrant | None = None,
    approval_verifier: ApprovalVerifier | None = None,
    now_epoch: int | None = None,
) -> PaymentReceipt:
    """Evaluate one intent and reserve budget when the decision is approved."""
    now = int(time.time()) if now_epoch is None else now_epoch

    approval_verified = False
    approval_verifier_failed = False

    def receipt(
        status: DecisionStatus,
        policy_hits: tuple[str, ...],
        risk_notes: tuple[str, ...] = (),
        *,
        budget_reservation_id: str | None = None,
    ) -> PaymentReceipt:
        return PaymentReceipt.create(
            ts_ms=now * 1000,
            status=status,
            intent=intent,
            session_id=scope.session_id,
            budget_scope_id=engine.budget_scope_id,
            policy_version=engine.policy_version,
            policy_hits=policy_hits,
            risk_notes=risk_notes,
            budget_reservation_id=budget_reservation_id,
            approval_id=approval.approval_id if approval_verified and approval else None,
            approved_by=approval.approved_by if approval_verified and approval else None,
        )

    if scope.paused:
        return receipt("blocked", ("session_paused",), ("session is paused",))

    if scope.is_expired(now):
        return receipt(
            "blocked",
            ("session_expired",),
            (f"session expired at epoch {scope.expires_at_epoch}",),
        )

    if intent.agent_id != scope.agent_id:
        return receipt(
            "blocked",
            ("agent_id_mismatch",),
            ("intent agent does not own this session",),
        )

    if scope.allowed_methods is not None:
        if not intent.intent_tag:
            return receipt(
                "blocked",
                ("intent_tag_required",),
                ("session requires an intent tag",),
            )
        if intent.intent_tag not in scope.allowed_methods:
            return receipt(
                "blocked",
                ("intent_not_in_session_scope",),
                (f"intent tag {intent.intent_tag!r} is not allowed",),
            )

    if intent.recipient.lower() not in scope.allowed_recipients:
        return receipt("blocked", ("session_recipient_not_allowed",))

    if intent.token.upper() not in scope.allowed_tokens:
        return receipt("blocked", ("session_token_not_allowed",))

    decision = engine.evaluate(
        recipient=intent.recipient,
        token=intent.token,
        amount_usd=intent.amount_usd,
        session_max_per_tx=scope.max_per_tx_usd,
        approval_verified=False,
    )
    if decision.status == "blocked":
        return receipt(decision.status, decision.policy_hits, decision.risk_notes)

    if decision.status == "pending_human":
        if approval is not None and approval_verifier is not None:
            try:
                approval_verified = bool(
                    approval_verifier.verify(
                        approval,
                        intent,
                        session_id=scope.session_id,
                        policy_version=engine.policy_version,
                        budget_scope_id=engine.budget_scope_id,
                        now_epoch=now,
                    )
                )
            except Exception:
                approval_verifier_failed = True

        if approval_verified:
            decision = engine.evaluate(
                recipient=intent.recipient,
                token=intent.token,
                amount_usd=intent.amount_usd,
                session_max_per_tx=scope.max_per_tx_usd,
                approval_verified=True,
            )
        else:
            notes = decision.risk_notes
            if approval is not None:
                notes += ("provided approval grant could not be verified",)
            if approval_verifier_failed:
                notes += ("approval verifier failed closed",)
            return receipt(decision.status, decision.policy_hits, notes)

    if decision.status != "approved":
        notes = decision.risk_notes
        return receipt(decision.status, decision.policy_hits, notes)

    if budget_store is None:
        return receipt(
            "blocked",
            ("budget_store_required",),
            ("approved decisions require an atomic budget store",),
        )

    try:
        budget = budget_store.reserve(
            session_id=scope.session_id,
            budget_scope_id=engine.budget_scope_id,
            intent_id=intent.intent_id,
            intent_hash=intent.canonical_digest(),
            amount_usd=intent.amount_usd,
            daily_limit_usd=scope.daily_budget_usd,
            weekly_limit_usd=engine.weekly_budget_usd,
            now_epoch=now,
        )
    except Exception:
        return receipt(
            "blocked",
            ("budget_store_error",),
            ("budget reservation failed closed",),
        )
    if not budget.approved or budget.reservation is None:
        return receipt(
            "blocked",
            (budget.policy_hit or "budget_rejected",),
            (budget.risk_note or "budget reservation failed",),
        )

    return receipt(
        "approved",
        decision.policy_hits + ("budget_reserved",),
        budget_reservation_id=budget.reservation.reservation_id,
    )
