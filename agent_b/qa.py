"""The Q&A service — retrieve, answer, log (S-B6). The transport-agnostic core Slack (S-B7) calls.

Ties the pieces together for one question: retrieve grounding (`agent_b.rag`), have the Answerer agent
draft a grounded, citing, refusing reply (`agent_b.agents.answerer`), and record the exchange in
`qa_log` (AD-20 traceability + the S-B9 eval). It is deliberately free of any Slack/HTTP concern so it
is fully testable offline and reusable by the eval harness and any future surface.

Refusal is the union of two guards: retrieval refuses when the top score is below `rag.min_score`, and
the answerer refuses when the passages don't actually answer the question. Either → no cited sources and
a refusal logged, so the KB never presents a fabricated answer as grounded (AD-30).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_b.agents.answerer import AnswererAgent
from agent_b.config import AgentBConfig
from agent_b.rag.embedder import Embedder
from agent_b.rag.retriever import Hit, retrieve
from agent_b.repository import AgentBRepository
from app.agents.llm import CallMetadata


@dataclass(frozen=True, slots=True)
class QaResult:
    qa_id: int
    answer: str
    hits: tuple[Hit, ...]
    refused: bool
    top_score: float


async def answer_question(
    question: str,
    *,
    repo: AgentBRepository,
    embedder: Embedder,
    answerer: AnswererAgent,
    config: AgentBConfig,
    metadata: CallMetadata,
    channel: str | None = None,
    user_id: str | None = None,
) -> QaResult:
    retrieval = retrieve(repo, embedder, question, config)
    # The catalog (every live doc's title + type) lets the answerer greet, list docs, and suggest the
    # closest related docs when nothing matched — without inventing anything (SKILL keeps it grounded).
    catalog = [(str(d["title"]), str(d["doc_type"])) for d in repo.all_documents()]
    result = await answerer.answer(question, retrieval.hits, catalog, metadata=metadata)
    # `refused` = no passage cleared the confidence bar, i.e. we did NOT serve a grounded doc answer.
    # The reply is still warm (greeting / "couldn't find it, here's what's related") — refusal here
    # means "no citations to show", which is why Sources are omitted for it.
    refused = retrieval.refused
    hits = () if refused else retrieval.hits
    qa_id = repo.log_qa(
        correlation_id=metadata.correlation_id,
        question=question,
        answer=result.text,
        cited_page_ids=[h.page_id for h in hits],
        refused=refused,
        channel=channel,
        user_id=user_id,
    )
    return QaResult(
        qa_id=qa_id,
        answer=result.text,
        hits=tuple(hits),
        refused=refused,
        top_score=retrieval.top_score,
    )
