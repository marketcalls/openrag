"""Stream bounded agent gathering while preserving deterministic release gates."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass

from openrag.modules.orchestration.agent_loop import (
    AgentFinishReason,
    AgentLoopProgress,
    AgentObservation,
    AgentPlanner,
    run_agent_loop,
)
from openrag.modules.orchestration.retrieval_tools import (
    RetrievalToolExecutor,
    merge_authoritative_evidence,
)
from openrag.modules.retrieval.service import RetrievalResult


@dataclass(frozen=True, slots=True)
class AgentGatherCompleted:
    result: RetrievalResult
    finish_reason: AgentFinishReason


AgentGatherEvent = AgentLoopProgress | AgentGatherCompleted
AgentGathererFactory = Callable[[str], "AgentGatherer"]


class AgentGatherer:
    """Join a planner and tenant-pinned tools for one user question."""

    def __init__(
        self,
        planner: AgentPlanner,
        executor: RetrievalToolExecutor,
    ) -> None:
        self._planner = planner
        self._executor = executor

    async def stream(
        self,
        *,
        query: str,
        initial_result: RetrievalResult,
        top_k: int,
        min_score: float,
        minimum_tool_calls: int = 1,
    ) -> AsyncIterator[AgentGatherEvent]:
        if not 1 <= minimum_tool_calls <= 4:
            raise ValueError("agent_minimum_tool_calls_invalid")
        initial = self._executor.seed(
            query=query,
            evidence=initial_result.evidence,
        )
        progress: asyncio.Queue[AgentLoopProgress] = asyncio.Queue(maxsize=8)

        def evidence_is_sufficient(
            observations: tuple[AgentObservation, ...],
        ) -> bool:
            tool_calls = max(0, len(observations) - 1)
            if tool_calls < minimum_tool_calls:
                return False
            return not merge_authoritative_evidence(
                query,
                self._executor.collected_evidence,
                top_k=top_k,
                min_score=min_score,
            ).no_answer

        loop_task = asyncio.create_task(
            run_agent_loop(
                self._planner,
                self._executor,
                initial_observations=(initial,),
                on_progress=progress.put_nowait,
                stop_when=evidence_is_sufficient,
                stop_on_empty_provenance=True,
            )
        )
        try:
            while not loop_task.done() or not progress.empty():
                next_progress = asyncio.create_task(progress.get())
                done, _pending = await asyncio.wait(
                    {loop_task, next_progress},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if next_progress in done:
                    yield next_progress.result()
                else:
                    next_progress.cancel()
                    with suppress(asyncio.CancelledError):
                        await next_progress
            loop_result = await loop_task
        finally:
            if not loop_task.done():
                loop_task.cancel()
                with suppress(asyncio.CancelledError):
                    await loop_task

        gathered_result = merge_authoritative_evidence(
            query,
            self._executor.collected_evidence,
            top_k=top_k,
            min_score=min_score,
        )
        # Legacy chunks have already passed their tenant and evidence gates but
        # intentionally do not carry authority-generation evidence identities.
        # A planner that adds no stronger evidence must never erase that valid
        # initial result and turn a grounded answer into a false refusal.
        result = (
            initial_result
            if not initial_result.no_answer and gathered_result.no_answer
            else gathered_result
        )
        yield AgentGatherCompleted(
            result=result,
            finish_reason=loop_result.finish_reason,
        )
