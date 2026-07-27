"""S-B6 — RAG index: chunk/embed/store, vector+graph retrieval, refusal, qa_log (offline, fakes)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_b.agents.answerer import AnswererAgent
from agent_b.config import AgentBConfig, load_agent_b_config
from agent_b.qa import answer_question
from agent_b.rag import chunk_text, index_vault, retrieve, strip_note_scaffolding
from agent_b.repository import AgentBRepository
from app.agents.llm import CallMetadata


class FakeEmbedder:
    """Deterministic bag-of-keywords embedding over a fixed vocabulary — no model, no network.

    Each text becomes a vector counting occurrences of a small vocab; cosine then reflects keyword
    overlap, which is enough to assert "the right note is retrieved" without a real model.
    """

    VOCAB = ("onboard", "billing", "invoice", "search", "note", "capture", "sync", "pin")

    def embed(self, texts):  # noqa: ANN001
        vectors = []
        for text in texts:
            low = text.lower()
            vec = [float(low.count(word)) for word in self.VOCAB]
            if not any(vec):
                vec = [1e-3] * len(self.VOCAB)  # avoid a zero vector (cosine undefined)
            vectors.append(vec)
        return vectors


class FakeLlm:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def complete(self, *, model, system, prompt, metadata):  # noqa: ANN001, ARG002
        self.calls += 1
        # Echo the passages it was actually given, so a test can assert grounding was passed in.
        return SimpleNamespace(text=self._text, model=model, input_tokens=1, output_tokens=1)


def _config(base=None) -> AgentBConfig:
    block = {
        "agent_b": {
            "space_key": "PM",
            "confluence_credentials_ref": "env:ALPHA_CONF",
            "include_folder_ids": ["F"],
            "embeddings": {"chunk_chars": 400, "chunk_overlap": 40},
            "rag": {"top_k": 3, "min_score": 0.2, "graph_expansion_hops": 1},
        }
    }
    if base:
        block["agent_b"].update(base)
    cfg = load_agent_b_config(block)
    assert cfg is not None
    return cfg


def _meta() -> CallMetadata:
    return CallMetadata(correlation_id="c1", prd_id="kb", agent_role="answerer")


def _seed(
    repo: AgentBRepository, page_id: str, title: str, body: str, *, parent_id: str = "F"
) -> None:
    content = f'---\ntitle: "{title}"\npage_id: "{page_id}"\n---\n\n# {title}\n\n{body}\n'
    repo.upsert_document(
        page_id=page_id,
        space_key="PM",
        title=title,
        doc_type="prd",
        vault_path=f"notes/{page_id}.md",
        content_hash=f"h-{page_id}-{hash(body) & 0xFFFF}",
        base_content=content,
        parent_id=parent_id,
        source_url=f"https://x/wiki/pages/{page_id}",
    )


def _corpus(repo: AgentBRepository) -> None:
    _seed(repo, "P1", "Onboarding PRD", "How to onboard a new user, the onboard onboard flow.")
    _seed(repo, "P2", "Billing PRD", "Billing and invoice handling. invoice billing billing.")
    _seed(repo, "P3", "Search PRD", "Full text search over every note; search search capture.")


def test_chunker_strips_scaffolding_and_windows() -> None:
    note = '---\ntitle: "T"\n---\n\n# T\n\n' + ("word " * 300) + "\n"
    body = strip_note_scaffolding(note)
    assert "title:" not in body and body.startswith("# T")
    chunks = chunk_text(body, chunk_chars=400, overlap=40)
    assert len(chunks) > 1  # long body → multiple overlapping windows
    assert all(len(c) <= 400 for c in chunks)


def test_index_is_incremental_by_content_hash() -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)
    embedder = FakeEmbedder()

    first = index_vault(repo, embedder, _config())
    assert first.embedded == 3 and first.skipped == 0 and first.chunks >= 3

    second = index_vault(repo, embedder, _config())
    assert second.embedded == 0 and second.skipped == 3  # unchanged corpus → nothing re-embedded
    repo.close()


async def test_retrieval_finds_the_right_note_and_answers_with_citation() -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)
    embedder = FakeEmbedder()
    index_vault(repo, embedder, _config())

    retrieval = retrieve(repo, embedder, "how do users onboard?", _config())
    assert not retrieval.refused
    assert retrieval.hits[0].page_id == "P1"  # the onboarding note ranks first

    llm = FakeLlm("Users onboard via the guided flow [1].")
    answerer = AnswererAgent(llm, model="test-model")
    result = await answer_question(
        "how do users onboard?",
        repo=repo,
        embedder=embedder,
        answerer=answerer,
        config=_config(),
        metadata=_meta(),
        channel="C1",
        user_id="U1",
    )

    assert not result.refused
    assert "[1]" in result.answer and llm.calls == 1
    assert result.hits[0].page_id == "P1"
    row = repo.get_qa(result.qa_id)
    assert row is not None and row["refused"] == 0
    assert "P1" in row["cited_page_ids"] and row["channel"] == "C1"
    repo.close()


async def test_unanswerable_question_refuses_but_still_replies_warmly() -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)
    embedder = FakeEmbedder()
    index_vault(repo, embedder, _config())

    # The model IS called now (to craft a warm, guiding reply) — but nothing grounded is served, so
    # the exchange is flagged `refused` (no citations) and the KB never fabricates an answer.
    llm = FakeLlm("I couldn't find a doc on that, but I can help with onboarding or billing.")
    answerer = AnswererAgent(llm, model="test-model")
    result = await answer_question(
        "what is the company holiday policy?",  # no vocab overlap → below min_score
        repo=repo,
        embedder=embedder,
        answerer=answerer,
        config=_config(base={"rag": {"top_k": 3, "min_score": 0.5, "graph_expansion_hops": 1}}),
        metadata=_meta(),
    )

    assert result.refused  # no passage cleared the bar → no grounded answer / no sources
    assert result.hits == ()
    assert llm.calls == 1  # but the model was consulted to reply warmly (not a cold sentinel)
    row = repo.get_qa(result.qa_id)
    assert row is not None and row["refused"] == 1 and row["answer"] is not None
    repo.close()


def test_graph_expansion_pulls_in_a_linked_neighbour() -> None:
    repo = AgentBRepository.open(":memory:")
    # P1 (onboarding) is the vector hit; P9 is linked to it but shares no query keywords.
    _seed(repo, "P1", "Onboarding PRD", "onboard onboard onboarding flow for a new user.")
    # P9 shares no query keyword — its only vocab word is 'sync', orthogonal to 'onboard'.
    _seed(repo, "P9", "Welcome Email", "A friendly greeting; sync sync sync across devices.")
    repo.add_link("P1", "P9", kind="restored", source="deterministic")
    embedder = FakeEmbedder()
    index_vault(repo, embedder, _config())

    retrieval = retrieve(repo, embedder, "onboard", _config())

    pages = {h.page_id: h.via for h in retrieval.hits}
    assert pages.get("P1") == "vector"
    assert pages.get("P9") == "graph"  # reached only by following the [[link]]
    repo.close()


def test_feedback_is_recorded() -> None:
    repo = AgentBRepository.open(":memory:")
    qa_id = repo.log_qa(
        correlation_id="c1", question="q", answer="a", cited_page_ids=["P1"], refused=False
    )
    repo.set_qa_feedback(qa_id, "up")
    assert repo.get_qa(qa_id)["feedback"] == "up"
    repo.close()


def test_empty_index_refuses() -> None:
    repo = AgentBRepository.open(":memory:")
    retrieval = retrieve(repo, FakeEmbedder(), "anything", _config())
    assert retrieval.refused and retrieval.hits == ()
    repo.close()


@pytest.mark.skip(reason="exercises the real fastembed model download; run manually, needs network")
def test_fastembed_smoke() -> None:  # pragma: no cover
    from agent_b.rag import FastEmbedEmbedder

    vecs = FastEmbedEmbedder("BAAI/bge-small-en-v1.5").embed(["hello world"])
    assert len(vecs) == 1 and len(vecs[0]) == 384


async def test_followup_uses_history_to_retrieve_the_right_doc() -> None:
    repo = AgentBRepository.open(":memory:")
    _corpus(repo)  # P1 onboarding, P2 billing, P3 search
    embedder = FakeEmbedder()
    index_vault(repo, embedder, _config())
    llm = FakeLlm("Because onboarding uses the guided flow [1].")
    answerer = AnswererAgent(llm, model="test-model")

    # A bare "why?" matches no doc on its own (below a strict floor) → would refuse. With history about
    # onboarding, the augmented retry folds in the prior turn and finds the onboarding doc.
    history = [("how do I onboard a new user?", "Use the guided flow.")]
    result = await answer_question(
        "why?",
        repo=repo,
        embedder=embedder,
        answerer=answerer,
        config=_config(base={"rag": {"top_k": 3, "min_score": 0.5, "graph_expansion_hops": 1}}),
        metadata=_meta(),
        history=history,
        conversation_key="dmA",
    )

    assert not result.refused
    assert (
        result.hits and result.hits[0].page_id == "P1"
    )  # reached only via the history-augmented retry
    assert repo.recent_qa(
        "dmA"
    )  # the turn was logged under the conversation key for the next follow-up
    repo.close()
