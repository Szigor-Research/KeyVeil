from __future__ import annotations

import math
from dataclasses import dataclass, field


def _normalized_set(values: frozenset[str], *, upper: bool = False) -> frozenset[str]:
    if not isinstance(values, (set, frozenset)) or any(
        not isinstance(value, str) for value in values
    ):
        raise ValueError("allowlists must contain text values")
    normalized = {
        value.strip().upper() if upper else value.strip().lower()
        for value in values
        if value.strip()
    }
    return frozenset(normalized)


@dataclass(frozen=True)
class SessionScope:
    """Delegated, fail-closed boundaries for one agent session."""

    session_id: str
    agent_id: str
    expires_at_epoch: int
    max_per_tx_usd: float
    daily_budget_usd: float
    allowed_recipients: frozenset[str] = field(default_factory=frozenset)
    allowed_tokens: frozenset[str] = field(default_factory=frozenset)
    allowed_methods: frozenset[str] | None = None
    paused: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not isinstance(self.agent_id, str):
            raise ValueError("session_id and agent_id must be text")
        session_id = self.session_id.strip()
        agent_id = self.agent_id.strip()
        if not session_id or not agent_id:
            raise ValueError("session_id and agent_id must not be empty")
        if (
            isinstance(self.expires_at_epoch, bool)
            or not isinstance(self.expires_at_epoch, int)
            or self.expires_at_epoch <= 0
        ):
            raise ValueError("expires_at_epoch must be positive")
        if (
            isinstance(self.max_per_tx_usd, bool)
            or isinstance(self.daily_budget_usd, bool)
            or not isinstance(self.max_per_tx_usd, (int, float))
            or not isinstance(self.daily_budget_usd, (int, float))
        ):
            raise ValueError("session budgets must be finite positive numbers")
        try:
            max_per_tx_usd = float(self.max_per_tx_usd)
            daily_budget_usd = float(self.daily_budget_usd)
        except (TypeError, ValueError) as error:
            raise ValueError("session budgets must be finite positive numbers") from error
        if not math.isfinite(max_per_tx_usd) or max_per_tx_usd <= 0:
            raise ValueError("max_per_tx_usd must be a finite positive number")
        if not math.isfinite(daily_budget_usd) or daily_budget_usd <= 0:
            raise ValueError("daily_budget_usd must be a finite positive number")

        recipients = _normalized_set(self.allowed_recipients)
        tokens = _normalized_set(self.allowed_tokens, upper=True)
        if not recipients:
            raise ValueError("allowed_recipients must not be empty (fail-closed session)")
        if not tokens:
            raise ValueError("allowed_tokens must not be empty (fail-closed session)")

        methods = None
        if self.allowed_methods is not None:
            methods = _normalized_set(self.allowed_methods)

        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "max_per_tx_usd", max_per_tx_usd)
        object.__setattr__(self, "daily_budget_usd", daily_budget_usd)
        object.__setattr__(self, "allowed_recipients", recipients)
        object.__setattr__(self, "allowed_tokens", tokens)
        object.__setattr__(self, "allowed_methods", methods)

    def is_expired(self, now_epoch: int) -> bool:
        return now_epoch >= self.expires_at_epoch
