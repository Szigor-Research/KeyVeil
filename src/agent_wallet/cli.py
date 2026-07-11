from __future__ import annotations

import time

from agent_wallet import (
    InMemoryBudgetStore,
    PaymentIntent,
    PolicyEngine,
    SessionScope,
    evaluate_payment,
)


def main() -> None:
    now = int(time.time())
    scope = SessionScope(
        session_id="cli_reference_session",
        agent_id="cli-reference-agent",
        expires_at_epoch=now + 3600,
        max_per_tx_usd=5.0,
        daily_budget_usd=20.0,
        allowed_recipients=frozenset({"0x1111111111111111111111111111111111111111"}),
        allowed_tokens=frozenset({"USDC"}),
        allowed_methods=frozenset({"api_quota"}),
    )
    engine = PolicyEngine.from_defaults(
        policy_version="cli-reference-v1",
        budget_scope_id="cli-reference-organization",
        approval_threshold_usd=3.0,
        whitelist_recipients=scope.allowed_recipients,
        allowed_tokens=scope.allowed_tokens,
    )
    intent = PaymentIntent(
        intent_id="intent_cli_demo",
        task_id="task_cli_demo",
        agent_id=scope.agent_id,
        recipient="0x1111111111111111111111111111111111111111",
        token="USDC",
        amount_usd=2.5,
        reason="Synthetic API quota",
        intent_tag="api_quota",
    )
    receipt = evaluate_payment(
        scope,
        engine,
        intent,
        budget_store=InMemoryBudgetStore(),
        now_epoch=now,
    )
    print(receipt.to_json())


if __name__ == "__main__":
    main()
