from __future__ import annotations

import time
from dataclasses import dataclass

from agent_wallet import (
    ApprovalGrant,
    HmacApprovalAuthority,
    PaymentIntent,
    PolicyEngine,
    SessionScope,
)

VENDOR_MAIN = "0x1111111111111111111111111111111111111111"
VENDOR_ALT = "0x2222222222222222222222222222222222222222"
UNKNOWN = "0xdead00000000000000000000000000000000beef"
DEMO_APPROVAL_KEY = bytes(range(32))


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    expected_status: str
    log_lines: tuple[str, ...]
    scope: SessionScope
    engine: PolicyEngine
    intent: PaymentIntent
    approval: ApprovalGrant | None = None
    approval_authority: HmacApprovalAuthority | None = None
    committed_today_usd: tuple[float, ...] = ()


def _now_plus(hours: int = 24) -> int:
    return int(time.time()) + hours * 3600


def demo_scope(
    *,
    max_per_tx_usd: float = 5.0,
    daily_budget_usd: float = 20.0,
    paused: bool = False,
    allowed_methods: frozenset[str] | None = None,
) -> SessionScope:
    return SessionScope(
        session_id="reference_session",
        agent_id="reference-agent-01",
        expires_at_epoch=_now_plus(),
        max_per_tx_usd=max_per_tx_usd,
        daily_budget_usd=daily_budget_usd,
        allowed_recipients=frozenset({VENDOR_MAIN, VENDOR_ALT}),
        allowed_tokens=frozenset({"USDC"}),
        allowed_methods=allowed_methods,
        paused=paused,
    )


def demo_engine(*, approval_threshold_usd: float = 20.0) -> PolicyEngine:
    return PolicyEngine.from_defaults(
        policy_version="reference-policy-v1",
        budget_scope_id="reference-organization",
        approval_threshold_usd=approval_threshold_usd,
        whitelist_recipients=frozenset({VENDOR_MAIN, VENDOR_ALT}),
        allowed_tokens=frozenset({"USDC"}),
        weekly_budget_usd=100.0,
    )


def _intent(
    scenario_id: str,
    *,
    recipient: str = VENDOR_MAIN,
    token: str = "USDC",
    amount_usd: float,
    reason: str,
    intent_tag: str,
) -> PaymentIntent:
    return PaymentIntent(
        intent_id=f"intent_{scenario_id}",
        task_id=f"task_{scenario_id}",
        agent_id="reference-agent-01",
        recipient=recipient,
        token=token,
        amount_usd=amount_usd,
        reason=reason,
        intent_tag=intent_tag,
    )


def build_scenario(scenario_id: str) -> Scenario:
    sid = scenario_id.strip().lower().replace("-", "_")

    if sid == "approve_small":
        return Scenario(
            scenario_id=sid,
            title="Small storage allowance",
            expected_status="approved",
            log_lines=(
                "Intent received from reference-agent-01",
                "Recipient and token match the session allowlists",
                "Amount is below the verified-approval threshold",
                "Daily and weekly budget reserved atomically",
            ),
            scope=demo_scope(allowed_methods=frozenset({"storage", "compute", "api_quota"})),
            engine=demo_engine(),
            intent=_intent(
                sid,
                amount_usd=2.0,
                reason="Synthetic object-storage allowance",
                intent_tag="storage",
            ),
        )

    if sid == "block_huge":
        return Scenario(
            scenario_id=sid,
            title="Per-transaction limit",
            expected_status="blocked",
            log_lines=(
                "Intent requests 500 USDC",
                "Session limit is 5 USDC per transaction",
                "Decision stops before budget reservation",
            ),
            scope=demo_scope(allowed_methods=frozenset({"compute"})),
            engine=demo_engine(),
            intent=_intent(
                sid,
                amount_usd=500.0,
                reason="Synthetic oversized compute prepayment",
                intent_tag="compute",
            ),
        )

    if sid == "block_recipient":
        return Scenario(
            scenario_id=sid,
            title="Recipient allowlist",
            expected_status="blocked",
            log_lines=(
                "Intent targets an address outside the delegated scope",
                "Session recipient gate rejects the request",
            ),
            scope=demo_scope(allowed_methods=frozenset({"storage"})),
            engine=demo_engine(),
            intent=_intent(
                sid,
                recipient=UNKNOWN,
                amount_usd=2.0,
                reason="Synthetic unknown-recipient payment",
                intent_tag="storage",
            ),
        )

    if sid == "block_token":
        return Scenario(
            scenario_id=sid,
            title="Token allowlist",
            expected_status="blocked",
            log_lines=(
                "Intent requests WETH while the session allows USDC only",
                "Session token gate rejects the request",
            ),
            scope=demo_scope(allowed_methods=frozenset({"api_quota"})),
            engine=demo_engine(),
            intent=_intent(
                sid,
                token="WETH",
                amount_usd=1.0,
                reason="Synthetic API metering charge",
                intent_tag="api_quota",
            ),
        )

    if sid == "block_intent":
        return Scenario(
            scenario_id=sid,
            title="Intent method scope",
            expected_status="blocked",
            log_lines=(
                "Intent tag is p2p_transfer",
                "Session delegates storage, compute, and API quota only",
                "Method-scope gate rejects the request",
            ),
            scope=demo_scope(allowed_methods=frozenset({"storage", "compute", "api_quota"})),
            engine=demo_engine(),
            intent=_intent(
                sid,
                recipient=VENDOR_ALT,
                amount_usd=1.0,
                reason="Synthetic peer-to-peer transfer",
                intent_tag="p2p_transfer",
            ),
        )

    if sid == "pending_human":
        return Scenario(
            scenario_id=sid,
            title="Verified approval required",
            expected_status="pending_human",
            log_lines=(
                "Intent requests 30 USDC",
                "Policy requires verified approval at 20 USDC",
                "No approval grant is attached",
            ),
            scope=demo_scope(
                max_per_tx_usd=50.0,
                daily_budget_usd=100.0,
                allowed_methods=frozenset({"api_quota"}),
            ),
            engine=demo_engine(),
            intent=_intent(
                sid,
                amount_usd=30.0,
                reason="Synthetic premium API subscription",
                intent_tag="api_quota",
            ),
        )

    if sid == "approve_human":
        now = int(time.time())
        intent = _intent(
            sid,
            amount_usd=30.0,
            reason="Synthetic premium API subscription",
            intent_tag="api_quota",
        )
        authority = HmacApprovalAuthority(DEMO_APPROVAL_KEY)
        approval = authority.issue(
            intent=intent,
            session_id="reference_session",
            policy_version="reference-policy-v1",
            budget_scope_id="reference-organization",
            approved_by="reference-owner",
            now_epoch=now,
        )
        return Scenario(
            scenario_id=sid,
            title="Verified approval grant",
            expected_status="approved",
            log_lines=(
                "Intent requests 30 USDC",
                "Approval grant is bound to the canonical intent hash",
                "HMAC signature and expiry validate",
                "Budget reservation completes after approval verification",
            ),
            scope=demo_scope(
                max_per_tx_usd=50.0,
                daily_budget_usd=100.0,
                allowed_methods=frozenset({"api_quota"}),
            ),
            engine=demo_engine(),
            intent=intent,
            approval=approval,
            approval_authority=authority,
        )

    if sid == "block_daily_budget":
        return Scenario(
            scenario_id=sid,
            title="Daily budget exhausted",
            expected_status="blocked",
            log_lines=(
                "Existing committed spend is 19 USDC",
                "New intent requests 2 USDC against a 20 USDC daily limit",
                "Atomic budget reservation rejects the projected spend",
            ),
            scope=demo_scope(
                daily_budget_usd=20.0,
                allowed_methods=frozenset({"storage"}),
            ),
            engine=demo_engine(),
            intent=_intent(
                sid,
                amount_usd=2.0,
                reason="Synthetic storage overage",
                intent_tag="storage",
            ),
            committed_today_usd=(19.0,),
        )

    if sid == "session_paused":
        return Scenario(
            scenario_id=sid,
            title="Emergency session pause",
            expected_status="blocked",
            log_lines=(
                "Session pause is active",
                "All downstream policy and budget work is skipped",
            ),
            scope=demo_scope(paused=True, allowed_methods=frozenset({"storage"})),
            engine=demo_engine(),
            intent=_intent(
                sid,
                amount_usd=1.0,
                reason="Synthetic request after emergency pause",
                intent_tag="storage",
            ),
        )

    raise KeyError(f"unknown scenario: {scenario_id!r}")


def scenario_ids() -> tuple[str, ...]:
    return (
        "approve_small",
        "block_huge",
        "block_recipient",
        "block_token",
        "block_intent",
        "pending_human",
        "approve_human",
        "block_daily_budget",
        "session_paused",
    )


def list_scenario_meta() -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for sid in scenario_ids():
        scenario = build_scenario(sid)
        output.append(
            {
                "id": scenario.scenario_id,
                "title": scenario.title,
                "expected_status": scenario.expected_status,
                "max_per_tx_usd": scenario.scope.max_per_tx_usd,
                "daily_budget_usd": scenario.scope.daily_budget_usd,
                "approval_threshold_usd": scenario.engine.approval_threshold_usd,
                "budget_scope_id": scenario.engine.budget_scope_id,
                "allowed_tokens": sorted(scenario.scope.allowed_tokens),
            }
        )
    return output
