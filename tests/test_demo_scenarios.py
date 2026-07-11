import hashlib
import time

import pytest

from agent_wallet import InMemoryBudgetStore, evaluate_payment
from demo.scenarios import build_scenario, scenario_ids


@pytest.mark.parametrize("scenario_id", scenario_ids())
def test_demo_scenario_matches_declared_status(scenario_id: str) -> None:
    scenario = build_scenario(scenario_id)
    now = int(time.time())
    store = InMemoryBudgetStore()
    for index, amount in enumerate(scenario.committed_today_usd):
        preload = store.reserve(
            session_id=scenario.scope.session_id,
            budget_scope_id=scenario.engine.budget_scope_id,
            intent_id=f"preloaded_{index}",
            intent_hash=hashlib.sha256(f"preloaded_{index}".encode()).hexdigest(),
            amount_usd=amount,
            daily_limit_usd=scenario.scope.daily_budget_usd,
            weekly_limit_usd=scenario.engine.weekly_budget_usd,
            now_epoch=now,
        )
        assert preload.reservation is not None
        store.commit(preload.reservation.reservation_id)

    receipt = evaluate_payment(
        scenario.scope,
        scenario.engine,
        scenario.intent,
        budget_store=store,
        approval=scenario.approval,
        approval_verifier=scenario.approval_authority,
        now_epoch=now,
    )

    assert receipt.status == scenario.expected_status
    assert receipt.verify_hash()
