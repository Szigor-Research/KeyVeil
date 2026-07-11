from __future__ import annotations

import math
from dataclasses import dataclass

from .audit_receipt import DecisionStatus


@dataclass(frozen=True)
class PolicyDecision:
    status: DecisionStatus
    policy_hits: tuple[str, ...]
    risk_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyEngine:
    """Immutable organization policy evaluated in addition to a session scope."""

    approval_threshold_usd: float = 3.0
    policy_version: str = "default-v1"
    budget_scope_id: str = "default-policy-scope"
    whitelist_recipients: frozenset[str] | None = None
    allowed_tokens: frozenset[str] | None = None
    weekly_budget_usd: float | None = None
    global_paused: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not isinstance(self.budget_scope_id, str):
            raise ValueError("policy_version and budget_scope_id must be text")
        if isinstance(self.approval_threshold_usd, bool) or isinstance(
            self.weekly_budget_usd, bool
        ) or not isinstance(self.approval_threshold_usd, (int, float)):
            raise ValueError("approval_threshold_usd must be finite and non-negative")
        if self.weekly_budget_usd is not None and not isinstance(
            self.weekly_budget_usd, (int, float)
        ):
            raise ValueError("weekly_budget_usd must be a finite positive number")
        try:
            approval_threshold_usd = float(self.approval_threshold_usd)
            weekly_budget_usd = (
                float(self.weekly_budget_usd) if self.weekly_budget_usd is not None else None
            )
        except (TypeError, ValueError) as error:
            raise ValueError("policy budgets must be finite numbers") from error
        if not math.isfinite(approval_threshold_usd) or approval_threshold_usd < 0:
            raise ValueError("approval_threshold_usd must be finite and non-negative")
        if weekly_budget_usd is not None and (
            not math.isfinite(weekly_budget_usd) or weekly_budget_usd <= 0
        ):
            raise ValueError("weekly_budget_usd must be a finite positive number")
        policy_version = self.policy_version.strip()
        budget_scope_id = self.budget_scope_id.strip()
        if not policy_version or not budget_scope_id:
            raise ValueError("policy_version and budget_scope_id must not be empty")

        recipients = None
        if self.whitelist_recipients is not None:
            if not isinstance(self.whitelist_recipients, (set, frozenset)) or any(
                not isinstance(value, str) for value in self.whitelist_recipients
            ):
                raise ValueError("whitelist_recipients must contain text values")
            recipients = frozenset(
                value.strip().lower() for value in self.whitelist_recipients if value.strip()
            )
        tokens = None
        if self.allowed_tokens is not None:
            if not isinstance(self.allowed_tokens, (set, frozenset)) or any(
                not isinstance(value, str) for value in self.allowed_tokens
            ):
                raise ValueError("allowed_tokens must contain text values")
            tokens = frozenset(
                value.strip().upper() for value in self.allowed_tokens if value.strip()
            )

        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "budget_scope_id", budget_scope_id)
        object.__setattr__(self, "approval_threshold_usd", approval_threshold_usd)
        object.__setattr__(self, "weekly_budget_usd", weekly_budget_usd)
        object.__setattr__(self, "whitelist_recipients", recipients)
        object.__setattr__(self, "allowed_tokens", tokens)

    @classmethod
    def from_defaults(
        cls,
        *,
        approval_threshold_usd: float = 3.0,
        policy_version: str = "default-v1",
        budget_scope_id: str = "default-policy-scope",
        whitelist_recipients: frozenset[str] | None = None,
        allowed_tokens: frozenset[str] | None = None,
        weekly_budget_usd: float | None = None,
        global_paused: bool = False,
    ) -> PolicyEngine:
        return cls(
            approval_threshold_usd=approval_threshold_usd,
            policy_version=policy_version,
            budget_scope_id=budget_scope_id,
            whitelist_recipients=whitelist_recipients,
            allowed_tokens=allowed_tokens,
            weekly_budget_usd=weekly_budget_usd,
            global_paused=global_paused,
        )

    def evaluate(
        self,
        *,
        recipient: str,
        token: str,
        amount_usd: float,
        session_max_per_tx: float,
        approval_verified: bool,
    ) -> PolicyDecision:
        if self.global_paused:
            return PolicyDecision("blocked", ("global_pause",), ("organization policy is paused",))

        if (
            self.whitelist_recipients is not None
            and recipient.lower() not in self.whitelist_recipients
        ):
            return PolicyDecision(
                "blocked",
                ("policy_recipient_not_allowed",),
                ("recipient is outside the organization whitelist",),
            )

        if self.allowed_tokens is not None and token.upper() not in self.allowed_tokens:
            return PolicyDecision(
                "blocked",
                ("policy_token_not_allowed",),
                ("token is outside the organization allowlist",),
            )

        if amount_usd > session_max_per_tx:
            return PolicyDecision(
                "blocked",
                ("session_max_per_tx",),
                (f"amount {amount_usd} exceeds session limit {session_max_per_tx}",),
            )

        if amount_usd >= self.approval_threshold_usd and not approval_verified:
            return PolicyDecision(
                "pending_human",
                ("approval_required",),
                ("a verified approval grant is required",),
            )

        approval_hit = "approval_verified" if approval_verified else "approval_not_required"
        return PolicyDecision("approved", ("within_session_limit", approval_hit))
