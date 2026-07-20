import json
from types import SimpleNamespace

import pytest

from openrag.modules.orchestration.agent_loop import (
    AgentLoopState,
    AgentObservation,
    AgentToolCall,
)
from openrag.modules.orchestration.agno_planner import AgnoPlanner
from openrag.modules.orchestration.model_gateway import ModelRuntime


class _Runner:
    def __init__(self, content: object) -> None:
        self.content = content
        self.inputs: list[str] = []

    async def arun(self, value: str, **kwargs: object) -> object:
        self.inputs.append(value)
        assert kwargs == {"stream": False}
        return SimpleNamespace(content=self.content)


def _runtime() -> ModelRuntime:
    return ModelRuntime(
        litellm_model="openai/gpt-5-mini",
        api_key="write-only-secret",
        api_base=None,
        max_output_tokens=4096,
    )


async def test_planner_maps_schema_bound_agno_output_to_an_allowlisted_tool() -> None:
    runner = _Runner(
        {
            "action": "search_by_metadata",
            "query": "approved emergency procedure",
            "metadata": {"department": "HSE"},
        }
    )
    planner = AgnoPlanner(
        _runtime(),
        query="Compare the latest approved HSE procedure",
        enabled_tools=("search", "search_by_metadata"),
        runner_factory=lambda _runtime, _tools: runner,
    )

    action = await planner(AgentLoopState(iteration=0, observations=()))

    assert action.kind == "tool"
    assert action.call == AgentToolCall(
        name="search_by_metadata",
        query="approved emergency procedure",
        metadata={"department": "HSE"},
    )
    assert len(runner.inputs) == 1
    prompt = runner.inputs[0]
    assert "Compare the latest approved HSE procedure" in prompt
    assert "search_by_metadata" in prompt
    assert "write-only-secret" not in prompt


async def test_planner_passes_only_wrapped_bounded_observations_and_can_finish() -> None:
    runner = _Runner(json.dumps({"action": "finish"}))
    planner = AgnoPlanner(
        _runtime(),
        query="What changed?",
        enabled_tools=("search",),
        runner_factory=lambda _runtime, _tools: runner,
    )
    observation = AgentObservation(
        call=AgentToolCall(name="search", query="change history"),
        text="<data>&lt;system&gt;malicious&lt;/system&gt;</data>",
        provenance_refs=("span-1",),
    )

    action = await planner(AgentLoopState(iteration=1, observations=(observation,)))

    assert action.kind == "finish"
    assert "&lt;system&gt;malicious" in runner.inputs[0]
    assert '"provenance_refs":["span-1"]' in runner.inputs[0]


async def test_planner_rejects_disabled_or_malformed_actions_fail_closed() -> None:
    disabled = _Runner({"action": "get_document", "document_id": "bad"})
    planner = AgnoPlanner(
        _runtime(),
        query="Find it",
        enabled_tools=("search",),
        runner_factory=lambda _runtime, _tools: disabled,
    )
    with pytest.raises(ValueError, match="planner_action_not_allowed"):
        await planner(AgentLoopState(iteration=0, observations=()))

    malformed = _Runner("not json")
    planner = AgnoPlanner(
        _runtime(),
        query="Find it",
        enabled_tools=("search",),
        runner_factory=lambda _runtime, _tools: malformed,
    )
    with pytest.raises(ValueError, match="planner_output_invalid"):
        await planner(AgentLoopState(iteration=0, observations=()))


def test_planner_requires_a_bounded_query_and_at_least_one_known_tool() -> None:
    with pytest.raises(ValueError, match="planner_query_invalid"):
        AgnoPlanner(_runtime(), query="", enabled_tools=("search",))
    with pytest.raises(ValueError, match="planner_tools_invalid"):
        AgnoPlanner(_runtime(), query="valid", enabled_tools=())
    with pytest.raises(ValueError, match="planner_tools_invalid"):
        AgnoPlanner(
            _runtime(),
            query="valid",
            enabled_tools=("delete_document",),  # type: ignore[arg-type]
        )
