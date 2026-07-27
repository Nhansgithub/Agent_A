"""Agent B's LLM agents (S-B3+). The only `agent_b` layer that reaches the shared LLM runtime.

Each agent = a per-role `SKILL.md` (the tuning surface) over `app.agents.llm.LlmClient`, traced
(AD-20), with its model id from config (AD-17). Mirrors Agent A's agent shape.
"""
