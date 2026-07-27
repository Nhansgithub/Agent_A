"""S-B7 — Slack Q&A handler: allow-list, grounded reply + Sources, refusal, feedback (offline).

No `slack_bolt` import anywhere here — the handler is transport-agnostic, so the demo surface is fully
testable with fake events. The bolt Socket-Mode wiring (agent_b/slack/app.py) is live-only glue.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_b.agents.answerer import AnswererAgent
from agent_b.config import AgentBConfig, load_agent_b_config
from agent_b.rag import index_vault
from agent_b.repository import AgentBRepository
from agent_b.slack import SlackQaHandler, SlackQuery
from tests.test_agent_b_rag import FakeEmbedder


class FakeLlm:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, *, model, system, prompt, metadata):  # noqa: ANN001, ARG002
        return SimpleNamespace(text=self._text, model=model, input_tokens=1, output_tokens=1)


def _config(allowed: list[str]) -> AgentBConfig:
    cfg = load_agent_b_config(
        {
            "agent_b": {
                "space_key": "PM",
                "confluence_credentials_ref": "env:ALPHA_CONF",
                "include_folder_ids": ["F"],
                "embeddings": {"chunk_chars": 400, "chunk_overlap": 40},
                "rag": {"top_k": 3, "min_score": 0.2, "graph_expansion_hops": 1},
                "slack": {"allowed_channel_ids": allowed},
            }
        }
    )
    assert cfg is not None
    return cfg


def _seed_and_index(repo: AgentBRepository, embedder: FakeEmbedder, config: AgentBConfig) -> None:
    content = (
        '---\ntitle: "Onboarding PRD"\npage_id: "P1"\n---\n\n# Onboarding PRD\n\n'
        "How to onboard a new user; the onboard onboard flow.\n"
    )
    repo.upsert_document(
        page_id="P1",
        space_key="PM",
        title="Onboarding PRD",
        doc_type="prd",
        vault_path="notes/P1.md",
        content_hash="h1",
        base_content=content,
        parent_id="F",
        source_url="https://x/wiki/pages/P1",
    )
    index_vault(repo, embedder, config)


def _handler(config: AgentBConfig, llm_text: str) -> tuple[SlackQaHandler, AgentBRepository]:
    repo = AgentBRepository.open(":memory:")
    embedder = FakeEmbedder()
    _seed_and_index(repo, embedder, config)
    answerer = AnswererAgent(FakeLlm(llm_text), model="test-model")
    return SlackQaHandler(repo=repo, embedder=embedder, answerer=answerer, config=config), repo


async def test_dm_gets_a_grounded_reply_with_sources() -> None:
    config = _config(allowed=[])
    handler, repo = _handler(config, "Onboard via the guided flow [1].")

    reply = await handler.handle_query(
        SlackQuery(text="how do I onboard?", channel="D1", user="U1", is_dm=True, message_ts="m1")
    )

    assert reply is not None
    assert "[1]" in reply.text
    assert "*Sources:*" in reply.text
    assert "<https://x/wiki/pages/P1|Onboarding PRD>" in reply.text  # Slack link to the note
    assert reply.thread_ts is None  # top-level DM → replies inline, not in a thread
    row = repo.get_qa(reply.qa_id)
    assert row is not None and row["refused"] == 0
    repo.close()


async def test_dm_replies_inline_but_channel_mention_threads() -> None:
    config = _config(allowed=["C_ALLOWED"])
    handler, repo = _handler(config, "onboard via the flow [1]")

    dm = await handler.handle_query(
        SlackQuery(text="how do I onboard?", channel="D1", user="U1", is_dm=True, message_ts="m1")
    )
    mention = await handler.handle_query(
        SlackQuery(
            text="<@U0> how do I onboard?",
            channel="C_ALLOWED",
            user="U1",
            is_dm=False,
            message_ts="m2",
        )
    )
    in_thread = await handler.handle_query(
        SlackQuery(
            text="how do I onboard?",
            channel="D1",
            user="U1",
            is_dm=True,
            message_ts="m3",
            thread_ts="t9",
        )
    )

    assert dm is not None and dm.thread_ts is None  # DM → inline
    assert (
        mention is not None and mention.thread_ts == "m2"
    )  # channel → new thread under the mention
    assert (
        in_thread is not None and in_thread.thread_ts == "t9"
    )  # already in a thread → stays there
    repo.close()


async def test_mention_ignored_outside_allowed_channels() -> None:
    config = _config(allowed=["C_ALLOWED"])
    handler, repo = _handler(config, "answer [1]")

    blocked = await handler.handle_query(
        SlackQuery(text="<@U0> hi", channel="C_OTHER", user="U1", is_dm=False, message_ts="m1")
    )
    allowed = await handler.handle_query(
        SlackQuery(
            text="<@U0> how do I onboard?",
            channel="C_ALLOWED",
            user="U1",
            is_dm=False,
            message_ts="m2",
        )
    )

    assert blocked is None  # not in the allow-list → ignored
    assert allowed is not None and "*Sources:*" in allowed.text  # mention stripped, answered
    repo.close()


async def test_refusal_has_no_sources_section() -> None:
    # The bot now replies warmly even when it has no doc — but with no grounded hits, no Sources.
    warm = "I couldn't find a doc on that — want me to check onboarding instead?"
    config = _config(allowed=[])
    handler, repo = _handler(config, warm)

    # 'billing'/'invoice' are vocabulary the onboarding-only corpus doesn't contain → zero overlap.
    reply = await handler.handle_query(
        SlackQuery(
            text="how does billing invoice work?",
            channel="D1",
            user="U1",
            is_dm=True,
            message_ts="m1",
        )
    )

    assert reply is not None
    assert reply.text == warm  # the warm reply is shown verbatim...
    assert "*Sources:*" not in reply.text  # ...but with no grounded hits, no Sources section
    assert repo.get_qa(reply.qa_id)["refused"] == 1
    repo.close()


async def test_thumbs_reaction_records_feedback() -> None:
    config = _config(allowed=[])
    handler, repo = _handler(config, "Onboard via the flow [1].")
    reply = await handler.handle_query(
        SlackQuery(text="how do I onboard?", channel="D1", user="U1", is_dm=True, message_ts="m1")
    )
    assert reply is not None

    handler.remember("msg-ts-1", reply.qa_id)
    assert handler.record_feedback("msg-ts-1", "+1") is True
    assert repo.get_qa(reply.qa_id)["feedback"] == "up"
    assert handler.record_feedback("msg-ts-1", "-1") is True
    assert repo.get_qa(reply.qa_id)["feedback"] == "down"

    assert handler.record_feedback("unknown-ts", "+1") is False  # unremembered message → no-op
    assert handler.record_feedback("msg-ts-1", "eyes") is False  # not a thumbs reaction → no-op
    repo.close()


async def test_empty_question_is_ignored() -> None:
    config = _config(allowed=[])
    handler, repo = _handler(config, "x")
    reply = await handler.handle_query(
        SlackQuery(text="<@U0>   ", channel="D1", user="U1", is_dm=True, message_ts="m1")
    )
    assert reply is None  # a bare mention with no question → nothing to answer
    repo.close()
