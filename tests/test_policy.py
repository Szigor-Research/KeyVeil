import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent_wallet import (
    HmacApprovalAuthority,
    InMemoryBudgetStore,
    PaymentIntent,
    PolicyEngine,
    SessionScope,
    evaluate_payment,
)

NOW = 1_800_000_000
RECIPIENT = "0x1111111111111111111111111111111111111111"


def make_scope(
    *,
    max_per_tx_usd: float = 50.0,
    daily_budget_usd: float = 100.0,
    paused: bool = False,
) -> SessionScope:
    return SessionScope(
        session_id="session_test",
        agent_id="agent_test",
        expires_at_epoch=NOW + 3600,
        max_per_tx_usd=max_per_tx_usd,
        daily_budget_usd=daily_budget_usd,
        allowed_recipients=frozenset({RECIPIENT}),
        allowed_tokens=frozenset({"USDC"}),
        allowed_methods=frozenset({"api_quota"}),
        paused=paused,
    )


def make_engine(*, approval_threshold: float = 20.0) -> PolicyEngine:
    return PolicyEngine.from_defaults(
        policy_version="test-policy-v1",
        budget_scope_id="test-organization",
        approval_threshold_usd=approval_threshold,
        whitelist_recipients=frozenset({RECIPIENT}),
        allowed_tokens=frozenset({"USDC"}),
        weekly_budget_usd=250.0,
    )


def make_intent(intent_id: str, amount: float = 2.0) -> PaymentIntent:
    return PaymentIntent(
        intent_id=intent_id,
        task_id=f"task_{intent_id}",
        agent_id="agent_test",
        recipient=RECIPIENT,
        token="USDC",
        amount_usd=amount,
        reason="Synthetic test intent",
        intent_tag="api_quota",
    )


def evaluate(intent: PaymentIntent, **kwargs):
    return evaluate_payment(
        kwargs.pop("scope", make_scope()),
        kwargs.pop("engine", make_engine()),
        intent,
        budget_store=kwargs.pop("budget_store", InMemoryBudgetStore()),
        now_epoch=NOW,
        **kwargs,
    )


def issue_approval(
    authority: HmacApprovalAuthority,
    intent: PaymentIntent,
    *,
    scope: SessionScope | None = None,
    engine: PolicyEngine | None = None,
):
    bound_scope = scope or make_scope()
    bound_engine = engine or make_engine()
    return authority.issue(
        intent=intent,
        session_id=bound_scope.session_id,
        policy_version=bound_engine.policy_version,
        budget_scope_id=bound_engine.budget_scope_id,
        approved_by="owner",
        now_epoch=NOW,
    )


def test_approved_intent_reserves_budget_and_hashes_receipt():
    receipt = evaluate(make_intent("intent_approved"))

    assert receipt.status == "approved"
    assert receipt.budget_reservation_id.startswith("budget_")
    assert receipt.schema_version == "keyveil.receipt.v2"
    assert receipt.intent_hash == make_intent("intent_approved").canonical_digest()
    assert receipt.intent_schema_version == "keyveil.intent.v1"
    assert receipt.intent_tag == "api_quota"
    assert receipt.session_id == "session_test"
    assert receipt.budget_scope_id == "test-organization"
    assert receipt.policy_version == "test-policy-v1"
    assert receipt.ts_ms == NOW * 1000
    assert receipt.verify_hash()


@pytest.mark.parametrize("amount", [0.0, -1.0, math.nan, math.inf, -math.inf, True, "2", None])
def test_invalid_amounts_are_rejected_at_the_model_boundary(amount):
    with pytest.raises(ValueError, match="finite positive"):
        make_intent("intent_invalid", amount)


def test_session_requires_explicit_recipient_and_token_allowlists():
    with pytest.raises(ValueError, match="allowed_recipients"):
        SessionScope(
            session_id="session",
            agent_id="agent",
            expires_at_epoch=NOW + 60,
            max_per_tx_usd=5.0,
            daily_budget_usd=10.0,
            allowed_recipients=frozenset(),
            allowed_tokens=frozenset({"USDC"}),
        )


def test_text_fields_and_allowlists_reject_wrong_types_cleanly():
    with pytest.raises(ValueError, match="task_id must be text"):
        replace(make_intent("intent_bad_text"), task_id=None)

    with pytest.raises(ValueError, match="allowlists must contain text values"):
        replace(make_scope(), allowed_recipients=frozenset({1}))


def test_threshold_requires_verified_approval_grant():
    intent = make_intent("intent_review", 30.0)
    receipt = evaluate(intent)

    assert receipt.status == "pending_human"
    assert receipt.approval_id is None
    assert "approval_required" in receipt.policy_hits


def test_valid_hmac_approval_grant_is_bound_to_the_intent():
    intent = make_intent("intent_verified", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, intent)

    receipt = evaluate(intent, approval=grant, approval_verifier=authority)

    assert receipt.status == "approved"
    assert receipt.approval_id == grant.approval_id
    assert receipt.approved_by == "owner"
    assert "approval_verified" in receipt.policy_hits


def test_forged_approval_grant_does_not_cross_the_threshold():
    intent = make_intent("intent_forged", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, intent)
    forged = replace(grant, signature="0" * 64)

    receipt = evaluate(intent, approval=forged, approval_verifier=authority)

    assert receipt.status == "pending_human"
    assert "could not be verified" in receipt.risk_notes[-1]


def test_unknown_approval_schema_fails_closed():
    intent = make_intent("intent_unknown_approval_schema", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = replace(issue_approval(authority, intent), schema_version="keyveil.approval.v2")

    receipt = evaluate(intent, approval=grant, approval_verifier=authority)

    assert receipt.status == "pending_human"
    assert receipt.approval_id is None


def test_malformed_approval_grant_fails_closed_without_raising():
    intent = make_intent("intent_malformed_grant", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, intent)
    malformed = replace(grant, expires_at_epoch="invalid")

    receipt = evaluate(intent, approval=malformed, approval_verifier=authority)

    assert receipt.status == "pending_human"
    assert receipt.approval_id is None


def test_approval_verifier_failure_returns_a_pending_receipt():
    intent = make_intent("intent_verifier_failure", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, intent)

    class FailingVerifier:
        def verify(self, grant, intent, **context):
            raise RuntimeError("synthetic verifier outage")

    receipt = evaluate(intent, approval=grant, approval_verifier=FailingVerifier())

    assert receipt.status == "pending_human"
    assert "approval verifier failed closed" in receipt.risk_notes


@pytest.mark.parametrize(
    ("scope", "engine"),
    [
        (replace(make_scope(), session_id="session_substituted"), make_engine()),
        (make_scope(), replace(make_engine(), policy_version="test-policy-v2")),
        (make_scope(), replace(make_engine(), budget_scope_id="other-organization")),
    ],
)
def test_approval_grant_rejects_authorization_context_substitution(scope, engine):
    intent = make_intent("intent_context_bound", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, intent)

    receipt = evaluate(
        intent,
        scope=scope,
        engine=engine,
        approval=grant,
        approval_verifier=authority,
    )

    assert receipt.status == "pending_human"
    assert receipt.approval_id is None


@pytest.mark.parametrize(
    ("scope", "engine", "expected_hit"),
    [
        (make_scope(paused=True), make_engine(), "session_paused"),
        (make_scope(), replace(make_engine(), global_paused=True), "global_pause"),
    ],
)
def test_preapproval_gates_do_not_call_the_approval_verifier(scope, engine, expected_hit):
    intent = make_intent("intent_gate_before_approval", 30.0)
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, intent)

    class UnexpectedVerifier:
        calls = 0

        def verify(self, grant, intent, **context):
            self.calls += 1
            raise AssertionError("approval verifier must not be called")

    verifier = UnexpectedVerifier()
    receipt = evaluate(
        intent,
        scope=scope,
        engine=engine,
        approval=grant,
        approval_verifier=verifier,
    )

    assert receipt.status == "blocked"
    assert expected_hit in receipt.policy_hits
    assert verifier.calls == 0


def test_budget_reservation_accumulates_and_blocks_projected_spend():
    store = InMemoryBudgetStore()
    scope = make_scope(max_per_tx_usd=15.0, daily_budget_usd=20.0)
    first = evaluate(make_intent("intent_first", 12.0), scope=scope, budget_store=store)
    second = evaluate(make_intent("intent_second", 9.0), scope=scope, budget_store=store)

    assert first.status == "approved"
    assert second.status == "blocked"
    assert second.policy_hits == ("session_daily_budget",)


def test_weekly_budget_is_shared_across_sessions_in_the_same_budget_scope():
    store = InMemoryBudgetStore()
    engine = replace(make_engine(), weekly_budget_usd=20.0)
    first_scope = make_scope(max_per_tx_usd=15.0, daily_budget_usd=20.0)
    second_scope = replace(first_scope, session_id="session_second")

    first = evaluate(
        make_intent("intent_weekly_first", 12.0),
        scope=first_scope,
        engine=engine,
        budget_store=store,
    )
    second = evaluate(
        make_intent("intent_weekly_second", 9.0),
        scope=second_scope,
        engine=engine,
        budget_store=store,
    )

    assert first.status == "approved"
    assert second.status == "blocked"
    assert second.policy_hits == ("policy_weekly_budget",)


def test_concurrent_reservations_cannot_exceed_the_daily_budget():
    store = InMemoryBudgetStore()
    scope = make_scope(max_per_tx_usd=10.0, daily_budget_usd=10.0)
    engine = replace(make_engine(), weekly_budget_usd=100.0)

    def reserve(index: int):
        return evaluate(
            make_intent(f"intent_concurrent_{index}", 6.0),
            scope=scope,
            engine=engine,
            budget_store=store,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(reserve, range(2)))

    assert sorted(receipt.status for receipt in receipts) == ["approved", "blocked"]
    assert sum(receipt.budget_reservation_id is not None for receipt in receipts) == 1


def test_budget_reservation_is_idempotent_per_intent_id():
    store = InMemoryBudgetStore()
    scope = make_scope(daily_budget_usd=20.0)
    intent = make_intent("intent_idempotent", 2.0)

    first = evaluate(intent, scope=scope, budget_store=store)
    repeated = evaluate(intent, scope=scope, budget_store=store)
    remainder = evaluate(make_intent("intent_remainder", 18.0), scope=scope, budget_store=store)

    assert first.budget_reservation_id == repeated.budget_reservation_id
    assert remainder.status == "approved"


def test_intent_id_cannot_be_replayed_from_another_session_in_the_same_budget_scope():
    store = InMemoryBudgetStore()
    intent = make_intent("intent_session_bound", 2.0)

    first = evaluate(intent, budget_store=store)
    replay = evaluate(
        intent,
        scope=replace(make_scope(), session_id="session_replay"),
        budget_store=store,
    )

    assert first.status == "approved"
    assert replay.status == "blocked"
    assert replay.policy_hits == ("intent_session_mismatch",)


def test_intent_id_cannot_be_reused_with_a_different_payload():
    store = InMemoryBudgetStore()
    scope = make_scope(daily_budget_usd=20.0)
    original = make_intent("intent_payload_bound", 2.0)
    changed_recipient = replace(
        original,
        recipient="0x2222222222222222222222222222222222222222",
    )
    expanded_scope = replace(
        scope,
        allowed_recipients=frozenset({original.recipient, changed_recipient.recipient}),
    )
    expanded_engine = replace(make_engine(), whitelist_recipients=None)

    first = evaluate(
        original,
        scope=expanded_scope,
        engine=expanded_engine,
        budget_store=store,
    )
    changed = evaluate(
        changed_recipient,
        scope=expanded_scope,
        engine=expanded_engine,
        budget_store=store,
    )

    assert first.status == "approved"
    assert changed.status == "blocked"
    assert changed.policy_hits == ("intent_payload_mismatch",)


def test_approval_grant_rejects_payload_substitution():
    original = make_intent("intent_approval_bound", 30.0)
    substituted = replace(original, reason="Different payment purpose")
    authority = HmacApprovalAuthority(bytes(range(32)))
    grant = issue_approval(authority, original)

    receipt = evaluate(substituted, approval=grant, approval_verifier=authority)

    assert receipt.status == "pending_human"
    assert receipt.approval_id is None


def test_committed_budget_cannot_be_released():
    store = InMemoryBudgetStore()
    result = store.reserve(
        session_id="session_test",
        budget_scope_id="test-organization",
        intent_id="intent_committed",
        intent_hash="a" * 64,
        amount_usd=2.0,
        daily_limit_usd=20.0,
        weekly_limit_usd=100.0,
        now_epoch=NOW,
    )
    assert result.reservation is not None
    store.commit(result.reservation.reservation_id)

    with pytest.raises(ValueError, match="committed reservations cannot be released"):
        store.release(result.reservation.reservation_id)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session_id", " ", "session_id must not be empty"),
        ("budget_scope_id", " ", "budget_scope_id must not be empty"),
        ("intent_id", " ", "intent_id must not be empty"),
        ("intent_hash", "not-a-digest", "intent_hash must be a 64-character"),
    ],
)
def test_budget_store_rejects_invalid_identity_fields(field, value, message):
    arguments = {
        "session_id": "session_test",
        "budget_scope_id": "test-organization",
        "intent_id": "intent_valid",
        "intent_hash": "a" * 64,
        "amount_usd": 2.0,
        "daily_limit_usd": 20.0,
        "weekly_limit_usd": 100.0,
        "now_epoch": NOW,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        InMemoryBudgetStore().reserve(**arguments)


def test_approved_decision_fails_closed_without_budget_store():
    receipt = evaluate_payment(
        make_scope(),
        make_engine(),
        make_intent("intent_no_store"),
        budget_store=None,
        now_epoch=NOW,
    )

    assert receipt.status == "blocked"
    assert receipt.policy_hits == ("budget_store_required",)


def test_budget_store_failure_returns_a_blocked_receipt():
    class FailingBudgetStore:
        def reserve(self, **kwargs):
            raise RuntimeError("synthetic budget outage")

    receipt = evaluate(
        make_intent("intent_budget_failure"),
        budget_store=FailingBudgetStore(),
    )

    assert receipt.status == "blocked"
    assert receipt.policy_hits == ("budget_store_error",)
    assert receipt.risk_notes == ("budget reservation failed closed",)


@pytest.mark.parametrize(
    ("scope", "intent", "expected_hit"),
    [
        (make_scope(paused=True), make_intent("intent_paused"), "session_paused"),
        (
            replace(make_scope(), expires_at_epoch=NOW),
            make_intent("intent_expired"),
            "session_expired",
        ),
        (
            make_scope(),
            replace(make_intent("intent_agent"), agent_id="different-agent"),
            "agent_id_mismatch",
        ),
        (
            make_scope(),
            replace(make_intent("intent_token"), token="WETH"),
            "session_token_not_allowed",
        ),
        (
            make_scope(),
            replace(make_intent("intent_method"), intent_tag="p2p_transfer"),
            "intent_not_in_session_scope",
        ),
    ],
)
def test_session_gates_block_before_budget(scope, intent, expected_hit):
    receipt = evaluate(intent, scope=scope)

    assert receipt.status == "blocked"
    assert expected_hit in receipt.policy_hits


def test_receipt_hash_detects_mutation():
    receipt = evaluate(make_intent("intent_hash"))
    mutated = replace(receipt, amount_usd=receipt.amount_usd + 1)
    substituted_intent = replace(receipt, intent_hash="0" * 64)

    assert receipt.verify_hash()
    assert not mutated.verify_hash()
    assert not substituted_intent.verify_hash()


def test_receipt_contains_the_complete_canonical_intent():
    intent = make_intent("intent_complete")
    receipt = evaluate(intent)
    receipt_intent = PaymentIntent(
        intent_id=receipt.intent_id,
        task_id=receipt.task_id,
        agent_id=receipt.agent_id,
        recipient=receipt.recipient,
        token=receipt.token,
        amount_usd=receipt.amount_usd,
        reason=receipt.reason,
        intent_tag=receipt.intent_tag,
    )

    assert receipt.intent_hash == receipt_intent.canonical_digest()


def test_canonical_intent_hash_normalizes_numeric_amount_inputs():
    integer_amount = make_intent("intent_normalized_amount", 2)
    float_amount = make_intent("intent_normalized_amount", 2.0)

    assert integer_amount.amount_usd == 2.0
    assert integer_amount.canonical_digest() == float_amount.canonical_digest()
