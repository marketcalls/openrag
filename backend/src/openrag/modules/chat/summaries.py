"""Transactional scheduling and branch-safe summary selection."""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openrag.modules.chat.models import Chat, Message
from openrag.modules.chat.summary_models import (
    ConversationBranchSummary,
    ConversationSummaryJob,
)
from openrag.modules.tenancy.context import TenantContext


class AddSession(Protocol):
    def add(self, instance: object) -> None: ...


def schedule_branch_summary(
    session: AddSession,
    *,
    chat: Chat,
    assistant_message: Message,
) -> ConversationSummaryJob:
    if assistant_message.role != "assistant" or assistant_message.chat_id != chat.id:
        raise ValueError("summary jobs require an assistant message in the same chat")
    job = ConversationSummaryJob(
        org_id=chat.org_id,
        workspace_id=chat.workspace_id,
        chat_id=chat.id,
        user_id=chat.user_id,
        branch_head_message_id=assistant_message.id,
        requested_model_id=assistant_message.model_id,
    )
    session.add(job)
    return job


def choose_branch_summary(
    summaries: list[ConversationBranchSummary],
    branch: list[Message],
) -> ConversationBranchSummary | None:
    """Choose the farthest valid ancestor summary; never cross a sibling branch."""

    if not branch:
        return None
    positions = {message.id: index for index, message in enumerate(branch)}
    first = branch[0]
    candidates: list[tuple[int, int, ConversationBranchSummary]] = []
    for summary in summaries:
        head_position = positions.get(summary.branch_head_message_id)
        cover_position = positions.get(summary.covers_through_message_id)
        if (
            summary.status != "active"
            or summary.org_id != first.org_id
            or summary.workspace_id != first.workspace_id
            or summary.chat_id != first.chat_id
            or head_position is None
            or cover_position is None
            or cover_position > head_position
        ):
            continue
        candidates.append((cover_position, summary.source_message_count, summary))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def history_after_summary(
    branch: list[Message],
    summary: ConversationBranchSummary | None,
) -> list[Message]:
    if summary is None:
        return list(branch)
    positions = {message.id: index for index, message in enumerate(branch)}
    cover_position = positions.get(summary.covers_through_message_id)
    head_position = positions.get(summary.branch_head_message_id)
    if cover_position is None or head_position is None or cover_position > head_position:
        return list(branch)
    return branch[cover_position + 1 :]


async def load_branch_summary(
    session: AsyncSession,
    context: TenantContext,
    chat: Chat,
    branch: list[Message],
) -> ConversationBranchSummary | None:
    summaries = list(
        (
            await session.execute(
                select(ConversationBranchSummary).where(
                    ConversationBranchSummary.org_id == context.org_id,
                    ConversationBranchSummary.workspace_id == chat.workspace_id,
                    ConversationBranchSummary.chat_id == chat.id,
                    ConversationBranchSummary.user_id == context.user_id,
                    ConversationBranchSummary.status == "active",
                )
            )
        ).scalars()
    )
    return choose_branch_summary(summaries, branch)
