"""The bounded Executor half of HealthTrace Planner–Executor orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from backend.agent.resilience import invoke_with_resilience
from backend.agent.tool_registry import TOOL_BY_NAME, execution_plan_context


class ToolExecutionDenied(PermissionError):
    pass


@dataclass
class PlannerExecutor:
    """Executes only capabilities selected by a Planner execution plan.

    The class deliberately does not manufacture patient scope or confirmation.
    Callers must inject the scoped tool implementation and provide an
    idempotency key for any write capability.
    """

    execution_plan: dict

    @property
    def allowed_tools(self) -> set[str]:
        return set(self.execution_plan.get("allowed_tools") or [])

    @contextmanager
    def activate(self) -> Iterator[None]:
        with execution_plan_context(self.execution_plan):
            yield

    def invoke(
        self,
        tool_name: str,
        callback: Callable[[], str],
        *,
        confirmed: bool = False,
    ) -> str:
        if tool_name not in self.allowed_tools:
            raise ToolExecutionDenied(f"{tool_name} is not in the Planner allowlist")
        spec = TOOL_BY_NAME.get(tool_name)
        if spec and spec.requires_confirmation and not confirmed:
            raise ToolExecutionDenied(f"{tool_name} requires explicit user confirmation")
        return invoke_with_resilience(tool_name, callback)
