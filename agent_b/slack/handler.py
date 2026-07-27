"""The transport-agnostic Slack Q&A core (S-B7).

This is the whole *behaviour* of the bot with none of the Slack plumbing: a normalized query in, a
formatted reply out, feedback recorded. Keeping it free of `slack_bolt` is what lets the demo surface
be tested fully offline (fake events) — the bolt Socket-Mode wiring in `app.py` is a thin, live-only
adapter over this.

What it enforces:
  * **Channel allow-list** (AD-4 config): DMs are always allowed; a channel mention only if the channel
    is in `slack.allowed_channel_ids`. Everything else is ignored (returns `None`).
  * **Grounded answers with sources**: it delegates to `agent_b.qa.answer_question` (retrieve → answer →
    log), then renders a Slack-mrkdwn reply with a *Sources* section of note links, or the plain refusal.
  * **Feedback**: a 👍/👎 reaction on an answer maps to `up`/`down` in `qa_log`, resolved via the
    answer's message timestamp (the app remembers ts → qa_id when it posts).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from agent_b.agents.answerer import AnswererAgent
from agent_b.config import AgentBConfig
from agent_b.qa import QaResult, answer_question
from agent_b.rag.embedder import Embedder
from agent_b.repository import AgentBRepository
from app.agents.llm import CallMetadata

_MENTION = re.compile(r"<@[A-Z0-9]+>")
_UP = frozenset({"+1", "thumbsup", "thumbsup_all"})
_DOWN = frozenset({"-1", "thumbsdown"})
_HISTORY_TURNS = 6  # how many recent (question, answer) turns of memory to give the answerer


@dataclass(frozen=True, slots=True)
class SlackQuery:
    text: str
    channel: str
    user: str
    is_dm: bool
    message_ts: str = (
        ""  # the incoming message's own ts (used to open a thread under a channel mention)
    )
    thread_ts: str = ""  # the thread the incoming message is in, if any ("" = a top-level message)


@dataclass(frozen=True, slots=True)
class SlackReply:
    text: str
    thread_ts: str | None  # None → post directly (no thread); a ts → post in that thread
    qa_id: int


class SlackQaHandler:
    """Answers Slack questions over the vault and records thumbs feedback. No `slack_bolt` here."""

    __slots__ = ("_answerer", "_by_ts", "_config", "_embedder", "_repo")

    def __init__(
        self,
        *,
        repo: AgentBRepository,
        embedder: Embedder,
        answerer: AnswererAgent,
        config: AgentBConfig,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._answerer = answerer
        self._config = config
        self._by_ts: dict[str, int] = {}  # answer message ts -> qa_log id, for reaction feedback

    def is_allowed(self, channel: str, *, is_dm: bool) -> bool:
        return is_dm or channel in set(self._config.slack.allowed_channel_ids)

    async def handle_query(self, query: SlackQuery) -> SlackReply | None:
        """Answer one question, or return `None` if the channel isn't allowed / the text is empty."""
        if not self.is_allowed(query.channel, is_dm=query.is_dm):
            return None
        question = _MENTION.sub("", query.text).strip()
        if not question:
            return None
        metadata = CallMetadata(
            correlation_id=uuid.uuid4().hex, prd_id="agent_b_kb", agent_role="answerer"
        )
        # Conversation memory: a DM (or a channel thread) is one ongoing conversation. Load its recent
        # turns so follow-ups resolve; the exchange is logged under the same key for the next turn.
        key = _conversation_key(query)
        history = self._repo.recent_qa(key, limit=_HISTORY_TURNS)
        result = await answer_question(
            question,
            repo=self._repo,
            embedder=self._embedder,
            answerer=self._answerer,
            config=self._config,
            metadata=metadata,
            channel=query.channel,
            user_id=query.user,
            history=history,
            conversation_key=key,
        )
        return SlackReply(text=_format(result), thread_ts=_reply_thread(query), qa_id=result.qa_id)

    def remember(self, message_ts: str, qa_id: int) -> None:
        """The app calls this after posting an answer, so a later reaction can find its `qa_log` row."""
        self._by_ts[message_ts] = qa_id

    def record_feedback(self, message_ts: str, reaction: str) -> bool:
        """Map a 👍/👎 reaction on a remembered answer to `up`/`down`. Returns whether it applied."""
        qa_id = self._by_ts.get(message_ts)
        if qa_id is None:
            return False
        if reaction in _UP:
            self._repo.set_qa_feedback(qa_id, "up")
        elif reaction in _DOWN:
            self._repo.set_qa_feedback(qa_id, "down")
        else:
            return False
        return True


def _conversation_key(query: SlackQuery) -> str:
    """The id that groups messages into one conversation for memory (DMs + channel threads).

    A threaded exchange is keyed by its thread; a top-level DM is one ongoing conversation keyed by its
    channel; a top-level channel @-mention opens a thread, so its own message ts is that thread's root.
    Mirrors `_reply_thread` so a message and the reply it will thread under share the same key."""
    if query.thread_ts:
        return query.thread_ts
    if query.is_dm:
        return query.channel
    return query.message_ts


def _reply_thread(query: SlackQuery) -> str | None:
    """Where to post the reply. A message already inside a thread → stay in that thread (DM or channel).
    Otherwise: a top-level DM replies **directly** (no thread, so it's plainly visible); a top-level
    channel @-mention replies in a **new thread** under the mention (keeps the channel tidy)."""
    if query.thread_ts:
        return query.thread_ts
    if query.is_dm:
        return None  # top-level DM → reply inline, not in a thread
    return query.message_ts or None  # channel mention → open a thread under the message


def _format(result: QaResult) -> str:
    """Slack mrkdwn: the answer, then a Sources section of note links (omitted on a refusal)."""
    if result.refused or not result.hits:
        return result.answer
    sources = "\n".join(
        f"• <{hit.source_url}|{hit.title}>" if hit.source_url else f"• {hit.title}"
        for hit in result.hits
    )
    return f"{result.answer}\n\n*Sources:*\n{sources}"
