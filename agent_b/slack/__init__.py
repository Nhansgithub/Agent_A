"""Agent B Slack Q&A surface (S-B7).

`handler` is the transport-agnostic core (tested offline); `app.build_socket_mode_app` is the live bolt
wiring (imports `slack_bolt` lazily, so this package imports without the dependency present).
"""

from agent_b.slack.handler import SlackQaHandler, SlackQuery, SlackReply

__all__ = ["SlackQaHandler", "SlackQuery", "SlackReply", "build_socket_mode_app"]


def build_socket_mode_app(**kwargs: object):  # thin re-export that defers the slack_bolt import
    from agent_b.slack.app import build_socket_mode_app as _build

    return _build(**kwargs)  # type: ignore[arg-type]
