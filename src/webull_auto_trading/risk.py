from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str = ""


@dataclass(slots=True)
class RiskController:
    global_pause: bool = False
    paused_instances: set[str] = field(default_factory=set)
    locked_instances: dict[str, str] = field(default_factory=dict)

    def allow_instance(self, strategy_instance_id: str) -> RiskDecision:
        if self.global_pause:
            return RiskDecision(False, "global pause is enabled")
        if strategy_instance_id in self.paused_instances:
            return RiskDecision(False, "strategy instance is paused")
        if strategy_instance_id in self.locked_instances:
            return RiskDecision(False, self.locked_instances[strategy_instance_id])
        return RiskDecision(True)

    def set_global_pause(self, paused: bool) -> None:
        self.global_pause = paused

    def set_instance_pause(self, strategy_instance_id: str, paused: bool) -> None:
        if paused:
            self.paused_instances.add(strategy_instance_id)
        else:
            self.paused_instances.discard(strategy_instance_id)

    def lock_instance(self, strategy_instance_id: str, reason: str) -> None:
        self.locked_instances[strategy_instance_id] = reason


def daily_loss_limit_hit(
    *,
    day_start_equity: float,
    current_equity: float,
    daily_loss_limit_percent: float,
) -> bool:
    if daily_loss_limit_percent <= 0 or day_start_equity <= 0:
        return False
    loss_percent = (day_start_equity - current_equity) / day_start_equity * 100.0
    return loss_percent >= daily_loss_limit_percent
