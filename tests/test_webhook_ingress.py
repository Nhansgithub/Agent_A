"""Stories 1.4 / 1.5 / 1.6 — webhook ingress, idempotency, and tenant routing (AD-8, AD-9, AD-3)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domain.dedupe import DedupeKey, dedupe_key_for
from app.domain.stage import Stage
from app.domain.state import PrdState
from app.repository import Repository
from app.repository.database import Database
from app.router import TenantRouter
from app.webhooks.events import (
    EventType,
    JiraIssueUpdatedEvent,
    UnsupportedEvent,
    parse_event,
)
from app.webhooks.ingress import IngressOutcome, WebhookIngress
from app.webhooks.signature import (
    InvalidSignature,
    compute_hmac,
    verify_signature,
)

SECRET = "test-shared-secret"
ENV = {"WEBHOOK_SHARED_SECRET": SECRET}


@pytest.fixture
def repository() -> Repository:
    return Repository(Database(":memory:"))


@pytest.fixture
def ingress(registry, repository) -> WebhookIngress:
    return WebhookIngress(registry, repository, env=ENV)


# ---------------------------------------------------------------------------------------------
# Payload builders — the documented Atlassian Cloud webhook shapes.
# ---------------------------------------------------------------------------------------------


def page_payload(
    *, page_id: str = "page-1", version: int = 1, title: str = "final_PRD_Widget", **extra: Any
) -> dict[str, Any]:
    payload = {
        "webhookEvent": "page_created",
        "page": {
            "id": page_id,
            "title": title,
            "version": {"number": version},
            "parentId": "folder-source-1",
            "spaceKey": "SPACE",
            "history": {"createdBy": {"accountId": "acct-uploader-1"}},
        },
    }
    payload["page"].update(extra)
    return payload


def comment_payload(*, comment_id: str = "c-1", issue_key: str = "TESTREV-1") -> dict[str, Any]:
    return {
        "webhookEvent": "comment_created",
        "issue": {"key": issue_key, "fields": {"project": {"key": issue_key.split("-")[0]}}},
        "comment": {
            "id": comment_id,
            "author": {"accountId": "acct-pm-1"},
            "body": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Looks good"}]}
                ],
            },
        },
    }


def transition_payload(
    *,
    issue_key: str = "TESTREV-1",
    changelog_id: str = "chg-1",
    category: str = "done",
    field_name: str = "status",
) -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": issue_key,
            "fields": {
                "project": {"key": issue_key.split("-")[0]},
                "status": {"name": "Done", "statusCategory": {"key": category}},
            },
        },
        "changelog": {"id": changelog_id, "items": [{"field": field_name}]},
        "user": {"accountId": "acct-pm-1"},
    }


def signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    return body, {"X-Hub-Signature": f"sha256={compute_hmac(SECRET, body)}"}


# ---------------------------------------------------------------------------------------------
# Story 1.4 — signature validation. A spoofed request must never trigger an Atlassian write.
# ---------------------------------------------------------------------------------------------


def test_valid_hmac_signature_passes() -> None:
    body = b'{"a":1}'
    verify_signature(
        secret=SECRET,
        body=body,
        headers={"X-Hub-Signature": f"sha256={compute_hmac(SECRET, body)}"},
    )


def test_hmac_without_the_sha256_prefix_is_accepted() -> None:
    body = b'{"a":1}'
    verify_signature(
        secret=SECRET, body=body, headers={"X-Hub-Signature": compute_hmac(SECRET, body)}
    )


def test_shared_secret_header_passes() -> None:
    verify_signature(secret=SECRET, body=b"{}", headers={"X-Webhook-Secret": SECRET})


def test_headers_are_matched_case_insensitively() -> None:
    verify_signature(secret=SECRET, body=b"{}", headers={"x-webhook-secret": SECRET})


def test_wrong_hmac_is_rejected() -> None:
    body = b'{"a":1}'
    with pytest.raises(InvalidSignature, match="did not match"):
        verify_signature(
            secret=SECRET,
            body=body,
            headers={"X-Hub-Signature": f"sha256={compute_hmac('wrong', body)}"},
        )


def test_tampered_body_is_rejected() -> None:
    """The HMAC authenticates the body, so a captured request cannot be replayed with new content."""
    original = b'{"amount":1}'
    headers = {"X-Hub-Signature": f"sha256={compute_hmac(SECRET, original)}"}
    with pytest.raises(InvalidSignature):
        verify_signature(secret=SECRET, body=b'{"amount":9999}', headers=headers)


def test_wrong_shared_secret_is_rejected() -> None:
    with pytest.raises(InvalidSignature, match="did not match"):
        verify_signature(secret=SECRET, body=b"{}", headers={"X-Webhook-Secret": "nope"})


def test_missing_signature_headers_are_rejected() -> None:
    with pytest.raises(InvalidSignature, match="cannot be authenticated"):
        verify_signature(secret=SECRET, body=b"{}", headers={})


def test_unconfigured_secret_refuses_to_serve() -> None:
    """An unauthenticated endpoint that triggers Atlassian writes must never be served."""
    with pytest.raises(InvalidSignature, match="no webhook secret is configured"):
        verify_signature(secret="", body=b"{}", headers={"X-Webhook-Secret": "anything"})


def test_invalid_signature_is_dropped_with_no_state_write(ingress, repository) -> None:
    """Story 1.4 AC — dropped with no side effects and no state write (AD-8 step 1)."""
    body = json.dumps(page_payload()).encode()

    result = ingress.handle(
        body=body, headers={"X-Webhook-Secret": "wrong"}, payload=page_payload()
    )

    assert result.outcome is IngressOutcome.REJECTED_SIGNATURE
    assert result.event is None and result.tenant is None
    assert repository.events.count() == 0
    assert repository.state.get("page-1") is None


def test_unsigned_request_never_reaches_parsing_or_routing(ingress, repository) -> None:
    result = ingress.handle(body=b"{}", headers={}, payload=page_payload())
    assert result.outcome is IngressOutcome.REJECTED_SIGNATURE
    assert repository.events.count() == 0


# ---------------------------------------------------------------------------------------------
# Event parsing.
# ---------------------------------------------------------------------------------------------


def test_page_created_parses() -> None:
    event = parse_event(page_payload(page_id="p9", version=3, title="final_PRD_X"))
    assert event.event_type is EventType.CONFLUENCE_PAGE_CREATED
    assert (event.page_id, event.version_number, event.title) == ("p9", 3, "final_PRD_X")
    assert event.creator_account_id == "acct-uploader-1"
    assert event.container_id == "folder-source-1"


def test_page_updated_parses() -> None:
    payload = page_payload()
    payload["webhookEvent"] = "page_updated"
    assert parse_event(payload).event_type is EventType.CONFLUENCE_PAGE_UPDATED


def test_page_labels_are_extracted_for_the_self_ingestion_guard() -> None:
    """AD-10 defense-in-depth needs the labels available at detection time."""
    event = parse_event(
        page_payload(metadata={"labels": {"results": [{"name": "agent-generated"}]}})
    )
    assert "agent-generated" in event.labels


def test_comment_body_adf_is_flattened_to_text() -> None:
    """Jira v3 returns ADF, not a string — the Feedback interpreter needs what the PM actually wrote."""
    event = parse_event(comment_payload())
    assert event.body_text == "Looks good"
    assert event.author_account_id == "acct-pm-1"


def test_issue_transition_to_done_is_recognised_by_category() -> None:
    """AD-13 / AD-15 — done-ness is `statusCategory == done`, never a literal status name."""
    event = parse_event(transition_payload(category="done"))
    assert isinstance(event, JiraIssueUpdatedEvent)
    assert event.moved_to_done


def test_transition_to_a_non_done_category_is_not_a_pass() -> None:
    assert not parse_event(transition_payload(category="indeterminate")).moved_to_done


def test_a_non_status_edit_is_not_a_transition() -> None:
    """Editing a description must not be mistaken for the PM passing the draft (FR-12)."""
    event = parse_event(transition_payload(field_name="description"))
    assert not event.transitioned_status
    assert not event.moved_to_done


def test_unsupported_event_raises() -> None:
    with pytest.raises(UnsupportedEvent):
        parse_event({"webhookEvent": "user_created"})


def test_unsupported_event_is_dropped_not_escalated(ingress) -> None:
    payload = {"webhookEvent": "attachment_created"}
    body, headers = signed(payload)
    result = ingress.handle(body=body, headers=headers, payload=payload)
    assert result.outcome is IngressOutcome.DROPPED_UNSUPPORTED


# ---------------------------------------------------------------------------------------------
# Story 1.6 — route-before-work tenant resolution (AD-3).
# ---------------------------------------------------------------------------------------------


def test_page_routes_by_container_folder(two_tenant_registry) -> None:
    router = TenantRouter(two_tenant_registry)
    event = parse_event(page_payload(parentId="folder-source-2"))
    decision = router.resolve(event)
    assert decision.tenant.project_id == "tenant_two"


def test_draft_and_published_folders_also_route(two_tenant_registry) -> None:
    """Routing answers *which tenant*; the FR-01 watched-folder check is a separate decision."""
    router = TenantRouter(two_tenant_registry)
    assert router.resolve(
        parse_event(page_payload(parentId="folder-draft-1"))
    ).tenant.project_id == ("tenant_one")


def test_jira_event_routes_by_project_key(two_tenant_registry) -> None:
    router = TenantRouter(two_tenant_registry)
    decision = router.resolve(parse_event(comment_payload(issue_key="OTHERREV-5")))
    assert decision.tenant.project_id == "tenant_two"


def test_unknown_folder_resolves_to_no_tenant(two_tenant_registry) -> None:
    router = TenantRouter(two_tenant_registry)
    decision = router.resolve(parse_event(page_payload(parentId="folder-nobody-owns")))
    assert not decision.resolved
    assert "no configured tenant" in decision.reason


def test_unknown_jira_project_resolves_to_no_tenant(two_tenant_registry) -> None:
    router = TenantRouter(two_tenant_registry)
    assert not router.resolve(parse_event(comment_payload(issue_key="GHOST-1"))).resolved


def test_unrouted_event_is_dropped_with_no_side_effects(two_tenant_registry, repository) -> None:
    """Story 1.6 AC — an event resolving to no tenant is dropped; no work begins (AD-3)."""
    unrouted_ingress = WebhookIngress(two_tenant_registry, repository, env=ENV)
    payload = page_payload(parentId="folder-nobody-owns")
    body, headers = signed(payload)

    result = unrouted_ingress.handle(body=body, headers=headers, payload=payload)

    assert result.outcome is IngressOutcome.DROPPED_UNROUTED
    assert repository.events.count() == 0
    assert repository.state.get("page-1") is None


def test_missing_container_falls_back_to_the_single_tenant(registry) -> None:
    """The page-created payload may omit the container (PRD §13 Q3) — routing must still work."""
    router = TenantRouter(registry)
    payload = page_payload()
    del payload["page"]["parentId"]
    decision = router.resolve(parse_event(payload))
    assert decision.resolved
    assert "single configured tenant" in decision.reason


def test_missing_container_is_ambiguous_with_two_tenants(two_tenant_registry) -> None:
    router = TenantRouter(two_tenant_registry)
    payload = page_payload()
    del payload["page"]["parentId"]
    del payload["page"]["spaceKey"]
    decision = router.resolve(parse_event(payload))
    assert not decision.resolved
    assert "confluence_space_key" in decision.reason


# ---------------------------------------------------------------------------------------------
# Story 1.5 — the AD-9 composite dedupe key.
# ---------------------------------------------------------------------------------------------


def test_key_shape_is_the_ad9_composite() -> None:
    event = parse_event(page_payload(page_id="p1", version=7))
    assert dedupe_key_for("tenant_one", event).value == ("tenant_one:confluence.page_created:p1:7")


def test_confluence_key_uses_page_id_and_version_number() -> None:
    key = dedupe_key_for("t", parse_event(page_payload(page_id="p1", version=4)))
    assert (key.entity_id, key.version_marker) == ("p1", "4")


def test_jira_comment_key_uses_the_comment_id_and_needs_no_version() -> None:
    key = dedupe_key_for("t", parse_event(comment_payload(comment_id="c-99")))
    assert (key.entity_id, key.version_marker) == ("c-99", "")


def test_jira_transition_key_uses_issue_key_and_changelog_id() -> None:
    key = dedupe_key_for("t", parse_event(transition_payload(changelog_id="chg-77")))
    assert (key.entity_id, key.version_marker) == ("TESTREV-1", "chg-77")


def test_empty_key_component_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        DedupeKey(tenant_id="", event_type="e", entity_id="x")


def test_tenants_never_collide_on_the_same_entity() -> None:
    event = parse_event(page_payload())
    assert dedupe_key_for("tenant_one", event) != dedupe_key_for("tenant_two", event)


# ---------------------------------------------------------------------------------------------
# Story 1.5 — admission: recorded transactionally with the first state write, not on receipt.
# ---------------------------------------------------------------------------------------------


def test_admission_records_the_key_and_creates_the_state_row(repository) -> None:
    key = DedupeKey("tenant_one", "confluence.page_created", "page-1", "1")
    state = PrdState(prd_id="page-1", project_id="tenant_one")

    admitted = repository.admit(key, state)

    assert admitted is not None
    assert repository.state.require("page-1").stage is Stage.DETECTED
    assert repository.state.dedupe_keys_for("page-1") == (key.value,)


def test_second_admission_of_the_same_event_is_dropped(repository) -> None:
    """NFR-04 — Jira and Confluence redeliver routinely; the second delivery must not double-process."""
    key = DedupeKey("tenant_one", "confluence.page_created", "page-1", "1")
    repository.admit(key, PrdState(prd_id="page-1", project_id="tenant_one"))

    duplicate = repository.admit(key, PrdState(prd_id="page-1", project_id="tenant_one"))

    assert duplicate is None, "the duplicate lost the UNIQUE-insert race and was dropped"
    assert repository.events.count() == 1


def test_a_rename_is_a_new_version_and_is_not_suppressed(repository) -> None:
    """EH-04 — the whole reason the key carries a version marker rather than the page id alone."""
    original = DedupeKey("tenant_one", "confluence.page_updated", "page-1", "1")
    after_rename = DedupeKey("tenant_one", "confluence.page_updated", "page-1", "2")

    assert repository.events.record(original) is True
    assert repository.events.record(original) is False, "same version -> duplicate"
    assert repository.events.record(after_rename) is True, "new version -> re-enters the flow"


def test_failed_admission_rolls_back_the_state_row(repository) -> None:
    """A duplicate must not leave a half-written PRD row behind."""
    key = DedupeKey("tenant_one", "confluence.page_created", "page-1", "1")
    repository.admit(key, PrdState(prd_id="page-1", project_id="tenant_one"))
    before = repository.state.require("page-1")

    repository.admit(key, PrdState(prd_id="page-1", project_id="tenant_one", prd_title="OVERWRITE"))

    assert repository.state.require("page-1").correlation_id == before.correlation_id


def test_dedupe_keys_accumulate_as_a_read_only_projection(repository) -> None:
    """§10 `dedupe_keys` is a view over `processed_events`, never a second write target (AD-9)."""
    repository.admit(
        DedupeKey("tenant_one", "confluence.page_created", "page-1", "1"),
        PrdState(prd_id="page-1", project_id="tenant_one"),
    )
    repository.record_event_for(
        DedupeKey("tenant_one", "jira.comment_created", "c-1"), prd_id="page-1"
    )

    assert len(repository.state.dedupe_keys_for("page-1")) == 2


def test_gate_webhook_and_reconcile_poll_collide_on_the_same_transition(repository) -> None:
    """AD-22 — a poll and a webhook observing one transition derive the same key, so only one wins."""
    from_webhook = DedupeKey("tenant_one", "jira.issue_updated", "TESTREV-1", "chg-1")
    from_poll = DedupeKey("tenant_one", "jira.issue_updated", "TESTREV-1", "chg-1")

    assert repository.record_event_for(from_webhook, prd_id="page-1") is True
    assert repository.record_event_for(from_poll, prd_id="page-1") is False


# ---------------------------------------------------------------------------------------------
# The full pipeline, in order.
# ---------------------------------------------------------------------------------------------


def test_valid_event_is_accepted_with_tenant_and_key(ingress) -> None:
    payload = page_payload()
    body, headers = signed(payload)

    result = ingress.handle(body=body, headers=headers, payload=payload)

    assert result.accepted
    assert result.tenant.project_id == "tenant_one"
    assert result.dedupe_key.value == "tenant_one:confluence.page_created:page-1:1"
    assert result.event.title == "final_PRD_Widget"


def test_already_admitted_event_is_dropped_as_duplicate(ingress, repository) -> None:
    payload = page_payload()
    body, headers = signed(payload)
    first = ingress.handle(body=body, headers=headers, payload=payload)
    repository.admit(first.dedupe_key, PrdState(prd_id="page-1", project_id="tenant_one"))

    second = ingress.handle(body=body, headers=headers, payload=payload)

    assert second.outcome is IngressOutcome.DROPPED_DUPLICATE


def test_nothing_is_admitted_merely_by_passing_ingress(ingress, repository) -> None:
    """AD-9 — the key is recorded at *admission*, not on receipt.

    A crash between ingress and admission must leave the event redeliverable, not silently consumed.
    """
    payload = page_payload()
    body, headers = signed(payload)

    assert ingress.handle(body=body, headers=headers, payload=payload).accepted
    assert repository.events.count() == 0, "ingress must not record the key by itself"


# -- Confluence Automation cannot supply a page version (no such smart value) -------------------


def _page_payload_without_version() -> dict:
    """What a Confluence Automation rule can actually send: no version, because none exists."""
    return {
        "webhookEvent": "page_created",
        "page": {"id": "page-1", "title": "final_PRD_Widget", "version": {"number": ""}},
    }


def test_a_page_event_without_a_version_still_parses() -> None:
    """Requiring the version made the product untriggerable: no rule could ever satisfy it."""
    from app.webhooks.events import parse_event

    event = parse_event(_page_payload_without_version())

    assert event.page_id == "page-1"
    assert event.version_number is None
    assert event.needs_version_resolution is True


def test_an_unversioned_page_event_yields_no_dedupe_key() -> None:
    """The empty marker must never be keyed.

    One key per page forever would store the first edit and drop every later one as a duplicate,
    silently disabling the EH-04 rename re-entry the marker exists to enable.
    """
    from app.domain.events import ConfluencePageEvent, EventType

    event = ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_CREATED,
        page_id="page-1",
        version_number=None,
        title="final_PRD_Widget",
    )

    assert event.version_marker == ""
    assert event.needs_version_resolution is True


def test_a_resolved_version_restores_a_distinct_key_per_edit() -> None:
    """Once resolved, each version keys differently — which is what lets a rename re-enter."""
    from dataclasses import replace

    from app.domain.dedupe import dedupe_key_for
    from app.domain.events import ConfluencePageEvent, EventType

    base = ConfluencePageEvent(
        event_type=EventType.CONFLUENCE_PAGE_UPDATED,
        page_id="page-1",
        version_number=None,
        title="final_PRD_Widget",
    )
    v2 = dedupe_key_for("tenant_one", replace(base, version_number=2))
    v3 = dedupe_key_for("tenant_one", replace(base, version_number=3))

    assert v2.value != v3.value
    assert v2.version_marker == "2" and v3.version_marker == "3"


# -- FR-16: a page-trashed event parses to the trashed event type ------------------------------


def test_a_page_trashed_payload_parses_as_a_trashed_event() -> None:
    from app.domain.events import EventType
    from app.webhooks.events import parse_event

    for name in ("page_trashed", "page_removed", "page_deleted"):
        event = parse_event({"webhookEvent": name, "page": {"id": "draft-1"}})
        assert event.event_type is EventType.CONFLUENCE_PAGE_TRASHED
        assert event.is_trashed_event
        assert event.page_id == "draft-1"


def test_first_traverses_a_list_index() -> None:
    """The ancestors.0.id container fallback must actually resolve (was dead code)."""
    from app.webhooks.events import _first

    payload = {"page": {"ancestors": [{"id": "folder-9"}, {"id": "space-root"}]}}
    assert _first(payload, "page.ancestors.0.id") == "folder-9"
    assert _first(payload, "page.ancestors.1.id") == "space-root"
    assert _first(payload, "page.ancestors.5.id") is None
