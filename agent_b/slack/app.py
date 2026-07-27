"""Slack Socket-Mode wiring for the Q&A bot (S-B7) — the thin, live-only adapter over the handler.

`slack_bolt` is imported **lazily inside** `build_socket_mode_app`, so importing this module (and the
offline test suite) never requires the package — only actually starting the bot does. Socket Mode needs
no public HTTPS endpoint, which suits the Droplet (AD-21) and means the bot works behind the firewall.

The event receipt is acknowledged by bolt immediately (< 3s, FR); the grounded answer is produced by
the injected `SlackQaHandler` and posted into the thread afterwards. Tokens are resolved from `env:`
refs by the caller (AD-4) and passed in — no secret is read here.
"""

from __future__ import annotations

from typing import Any

from agent_b.slack.handler import SlackQaHandler, SlackQuery


def build_socket_mode_app(
    *, handler: SlackQaHandler, bot_token: str, app_token: str
) -> tuple[Any, Any]:
    """Construct the bolt `AsyncApp` + a Socket-Mode runner wired to `handler`. Imports bolt lazily."""
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=bot_token)

    async def _answer(event: dict[str, Any], say: Any, *, is_dm: bool) -> None:
        query = SlackQuery(
            text=str(event.get("text") or ""),
            channel=str(event.get("channel") or ""),
            user=str(event.get("user") or ""),
            is_dm=is_dm,
            message_ts=str(event.get("ts") or ""),
            thread_ts=str(event.get("thread_ts") or ""),
        )
        reply = await handler.handle_query(query)
        if reply is None:
            return
        # unfurl_*: off — the Sources section already shows clean titled links; Slack's preview cards
        # would add one bulky (and login-gated, for Confluence) card per link. Only pass thread_ts when
        # the handler chose to thread — a DM reply omits it, so it posts inline.
        say_kwargs: dict[str, Any] = {
            "text": reply.text,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if reply.thread_ts:
            say_kwargs["thread_ts"] = reply.thread_ts
        posted = await say(**say_kwargs)
        posted_ts = (posted or {}).get("ts") if isinstance(posted, dict) else None
        if posted_ts:
            handler.remember(str(posted_ts), reply.qa_id)

    @app.event("app_mention")
    async def _on_mention(event: dict[str, Any], say: Any) -> None:  # pragma: no cover - live glue
        await _answer(event, say, is_dm=False)

    @app.event("message")
    async def _on_message(event: dict[str, Any], say: Any) -> None:  # pragma: no cover - live glue
        # Only direct-message channels; ignore the bot's own messages and edits/joins.
        if event.get("channel_type") == "im" and not event.get("bot_id"):
            await _answer(event, say, is_dm=True)

    @app.event("reaction_added")
    async def _on_reaction(event: dict[str, Any]) -> None:  # pragma: no cover - live glue
        item = event.get("item") or {}
        handler.record_feedback(str(item.get("ts") or ""), str(event.get("reaction") or ""))

    return app, AsyncSocketModeHandler(app, app_token)
