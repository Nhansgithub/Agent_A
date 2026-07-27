"""Agent B configuration (AD-4 mirror, AD-27).

A leaf package: the schema + the loader for the `agent_b:` block of `config/registry.yaml`. Imports
nothing from `app` or the rest of `agent_b`.
"""

from agent_b.config.loader import (
    AgentBConfigError,
    load_agent_b_config,
    load_agent_b_config_file,
)
from agent_b.config.schema import (
    AgentBConfig,
    EmbeddingsConfig,
    PublishConfig,
    RagConfig,
    SlackConfig,
)

__all__ = [
    "AgentBConfig",
    "AgentBConfigError",
    "EmbeddingsConfig",
    "PublishConfig",
    "RagConfig",
    "SlackConfig",
    "load_agent_b_config",
    "load_agent_b_config_file",
]
