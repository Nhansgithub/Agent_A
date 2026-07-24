# final_PRD_API Keys

## Problem
Integration partners currently authenticate with a shared account password, which cannot be rotated
or scoped and is a security liability.

## Solution
Per-partner API keys with defined scopes, created and revoked from a developer settings page.

## Requirements
- Generate a named API key with a chosen set of scopes.
- Show the key value exactly once at creation.
- List active keys with last-used timestamps; revoke any key immediately.
- Reject requests whose key lacks the required scope.

## Scope
In scope: key lifecycle and scope enforcement. Out of scope: OAuth flows, per-key rate limits.
