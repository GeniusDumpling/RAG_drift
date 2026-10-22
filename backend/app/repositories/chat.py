from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.chat import ChatSession, ChatTurn
from app.models.search import AgentCall, SearchQuery
from app.schemas.search import AnswerResponse, ChatSessionDetail, ChatSessionRead, ChatTurnRead


class ChatSessionNotFound(ValueError):
    pass


class ChatSessionBusy(ValueError):
    pass


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(self) -> ChatSessionRead:
        item = ChatSession(title="新对话")
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return ChatSessionRead.model_validate(item)

    async def list_sessions(self, limit: int, offset: int) -> list[ChatSessionRead]:
        items = (
            await self.session.scalars(
                select(ChatSession)
                .order_by(ChatSession.updated_at.desc(), ChatSession.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [ChatSessionRead.model_validate(item) for item in items]

    async def detail(self, session_id: UUID) -> ChatSessionDetail:
        item = await self.session.get(ChatSession, session_id)
        if item is None:
            raise ChatSessionNotFound("找不到对话")
        turns = (
            await self.session.scalars(
                select(ChatTurn)
                .where(ChatTurn.session_id == session_id)
                .order_by(ChatTurn.created_at, ChatTurn.id)
            )
        ).all()
        return ChatSessionDetail(
            session=ChatSessionRead.model_validate(item),
            turns=[ChatTurnRead.model_validate(turn) for turn in turns],
        )

    async def delete_session(self, session_id: UUID) -> None:
        # Serialize against begin_turn, which locks the same parent row before generating.
        item = await self.session.scalar(
            select(ChatSession).where(ChatSession.id == session_id).with_for_update()
        )
        if item is None:
            raise ChatSessionNotFound("找不到对话")
        pending = await self.session.scalar(
            select(ChatTurn).where(ChatTurn.session_id == session_id, ChatTurn.status == "pending")
        )
        if pending is not None and pending.created_at > utcnow() - timedelta(minutes=10):
            await self.session.rollback()
            raise ChatSessionBusy("此对话仍在生成回答，请先停止生成后再删除")
        query_ids = list(
            (
                await self.session.scalars(
                    select(ChatTurn.search_query_id)
                    .where(ChatTurn.session_id == session_id, ChatTurn.search_query_id.is_not(None))
                    .distinct()
                )
            ).all()
        )
        await self.session.delete(item)
        await self.session.flush()  # The database cascades deletion to this session's turns.
        if query_ids:
            # Answers also live in query diagnostics. Remove those snapshots, while protecting
            # a query if it happens to be referenced by another conversation.
            orphan_queries = select(SearchQuery.id).where(
                SearchQuery.id.in_(query_ids),
                ~exists().where(ChatTurn.search_query_id == SearchQuery.id),
            )
            await self.session.execute(
                delete(AgentCall).where(AgentCall.related_search_query_id.in_(orphan_queries))
            )
            await self.session.execute(
                delete(SearchQuery).where(SearchQuery.id.in_(orphan_queries))
            )
        await self.session.commit()

    async def begin_turn(self, session_id: UUID, query: str) -> ChatTurn:
        item = await self.session.scalar(
            select(ChatSession).where(ChatSession.id == session_id).with_for_update()
        )
        if item is None:
            raise ChatSessionNotFound("找不到对话")
        pending = await self.session.scalar(
            select(ChatTurn).where(ChatTurn.session_id == session_id, ChatTurn.status == "pending")
        )
        if pending is not None:
            # Recover requests abandoned by a process restart without keeping a session locked.
            if pending.created_at > utcnow() - timedelta(minutes=10):
                await self.session.rollback()
                raise ChatSessionBusy("此对话仍有回答正在生成，请稍后重试")
            pending.status = "error"
            pending.generation = {"status": "error", "reason": "request_expired"}
            await self.session.flush()
        if item.title == "新对话":
            item.title = query.strip()[:120]
        item.updated_at = utcnow()
        turn = ChatTurn(
            session_id=session_id, query=query, answer="", status="pending", supporting_evidence=[]
        )
        self.session.add(turn)
        await self.session.commit()
        await self.session.refresh(turn)
        return turn

    async def history(self, session_id: UUID) -> list[dict[str, str]]:
        turns = list(
            (
                await self.session.scalars(
                    select(ChatTurn)
                    .where(ChatTurn.session_id == session_id, ChatTurn.status == "completed")
                    .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
                    .limit(6)
                )
            ).all()
        )
        messages: list[dict[str, str]] = []
        for turn in reversed(turns):
            # Bounded history is conversational context, never factual evidence.
            messages.extend(
                [
                    {"role": "user", "content": turn.query[:1000]},
                    {"role": "assistant", "content": turn.answer[:2000]},
                ]
            )
        return messages

    async def finish_turn(
        self,
        turn: ChatTurn,
        response: AnswerResponse | None,
        *,
        status: str = "completed",
        partial_answer: str = "",
        reason: str | None = None,
    ) -> None:
        turn.status = status
        if response is not None:
            turn.search_query_id = response.query.id
            turn.answer = response.answer
            turn.supporting_evidence = [
                e.model_dump(mode="json") for e in response.supporting_evidence
            ]
            turn.generation = response.generation.model_dump(mode="json")
        else:
            turn.answer = partial_answer
            turn.generation = {"status": status, "reason": reason}
        item = await self.session.get(ChatSession, turn.session_id)
        if item is not None:
            item.updated_at = utcnow()
        await self.session.commit()
