# ADR-0018 — Caller authentication with Keycloak (OIDC)

**Status:** Accepted

**Scope:** SC-1-12 (Issue #150), proposed block 1. This is a decision record only. It does not
claim that authentication is implemented or proven. The role and permission model belongs to
SC-1-14; runnable Keycloak configuration and application wiring belong to a successor
implementation Story blocked by SC-1-12 and SC-1-14.

## Context

The application currently uses a placeholder caller identity. `project_access.user_id` is a
string, and the application has no user table. The placeholder is a development/test exception,
not authentication. The capability register therefore continues to report caller authentication
as `no evidence` until implementation and verification.

The human decisions recorded in Issue #150 on 2026-09-27 and 2026-10-01 establish self-hosted
Keycloak using OIDC, manually managed accounts without AD/LDAP federation, separate decisions for
identity and authorization, and a decision-only scope for SC-1-12. The human also decided that the
placeholder stays limited to development/test, is disabled by default, requires explicit opt-in,
and is refused outside those environments (P-4/P-5). The proposed identifier SC-1-12 remains a
block 1 proposal until gate 1 approves the plan entry (P-6).

## Decision proposals and recorded human approvals

### Z-1. OIDC contract

Use the OIDC Authorization Code flow with PKCE for browser sign-in. The frontend obtains an access
token for this API and sends it as a bearer token. The backend validates the access token locally
using Keycloak's published JWKS: signature, an exact configured issuer, the API audience, expiry,
and not-before when present. Allow at most 60 seconds of clock skew. Cache JWKS by issuer; when a
token has an unknown `kid`, refresh the JWKS once and reject the token if no matching valid key is
available. Never accept an ID token as an API access token. Use `sub` as the caller identifier;
do not use `preferred_username`, email, or a display name as an authorization key.

For every API request, also make an online token-revocation and account-activity check against
Keycloak (or its authoritative status endpoint). The request is accepted only when the token is
active and its account is enabled. Disabling an account must therefore refuse its next request,
including requests carrying a still-unexpired access token. A cached JWKS is used only to verify
the token signature; it is not evidence that the token or account remains active. If the online
status check is unavailable, fail closed with a service-unavailable response; never fall back to
cached status or the placeholder. The implementation Story must prove the account-disable behavior
on the next request and the failure behavior when status cannot be checked.

**Human-approved maximum access-token lifetime: 5 minutes (gate 1, 2026-10-01, Issue #150).**
Configure this as a hard maximum in Keycloak for this API; no client or realm override may issue
a longer access token. The authentication implementation Story must configure this maximum and
prove that issued access tokens do not exceed it. The online status check provides immediate
account-disable revocation; the lifetime is an additional bound on bearer-token exposure if a
token is stolen.

Source: Issue #150 Z-1 and human decision P-3; OIDC access is subject to NF-04 and NF-11 in
`Wymagania/Requirements_EN.md` §5.

### Z-2. Placeholder lifecycle

Keep `APP_ALLOW_PLACEHOLDER_IDENTITY` only as a development/test compatibility path. Its default
is `false`; enabling it requires an explicit configuration opt-in. Application startup must
refuse this mode whenever the configured environment is not development or test. It must never
be a fallback after a missing, invalid, or expired bearer token.

The successor authentication implementation Story removes the placeholder path after Keycloak
authentication is wired and its integration tests use a real Keycloak instance. Until that
removal, production and other non-development/test environments refuse placeholder mode.

Source: Issue #150 Z-2 and human decision P-5; ADR-0005 addenda 2026-09-18, which already require
the opt-in default to be false and the environment boundary to be enforced at startup.

### Z-3. Application user record

Do not add an application user table for authentication. Use the validated OIDC `sub` as the
value stored in the existing `project_access.user_id` string column, subject to SC-1-14 deciding
how access rows are granted and interpreted. Do not persist profile claims in the application
database. Account credentials and account lifecycle remain in Keycloak under its administrator's
manual management. The implementation Story must verify the provider's `sub` fits the existing
column contract; any schema change requires a separate migration decision.

Source: Issue #150 Z-3; existing `project_access.user_id` model and human decision P-3.

### Z-4. Personal-data impact

Keycloak is a new store of personal data about application users. The purpose is account
authentication and access control. Keep application-side identity data to the opaque `sub`; do
not persist email, username, or display name. Keycloak may process the account attributes needed
for its manually managed account and authentication lifecycle. The administrator must define a
retention rule for disabled accounts and account backups before production use; disabled accounts
must not remain indefinitely by default.

The proposed lawful basis is the controller's legitimate interest in securing access to the
staffing and profitability planning application (GDPR Article 6(1)(f)). The controller remains
accountable for the basis and must document it; the DPO must confirm it before production accounts
are created. This design does not require special-category personal data. A stable `sub` may
appear in security audit records only where a documented security purpose requires it;
application logs should use a non-reversible or access-restricted identifier. They must not
contain bearer tokens, the `Authorization` header, credentials, or profile claims. This
constrains diagnostics under NF-11;
it does not decide the separate audit-log feature under F-12.

Keycloak's database and backups must be encrypted at rest and access-restricted. This ADR records
the requirement, not evidence that infrastructure satisfies NF-04. The implementation/deployment
Stories must supply that evidence before production personal data is stored.

Source: Issue #150 Z-4; `Wymagania/Requirements_EN.md` §5 NF-04/NF-11; ADR-0019 §§2, 4, 8–9 for
the distinction between an application user and a named person, data minimization, and the
existing real-data gate. The proposed lawful basis is subject to controller accountability and
DPO confirmation before production.

### Z-5. Configuration as code

The successor implementation Story must provide a runnable, version-controlled Keycloak realm
configuration for development and integration tests, with no credentials or client secrets in
the repository. Client secrets must be supplied through the approved secret mechanism. The same
OIDC protocol is used in development/test and production; issuer and realm configuration differ
by environment. This ADR sets those constraints only: realm export, runnable configuration,
secret wiring, Keycloak startup, and application integration are outside SC-1-12.

Source: Issue #150 Z-5 and human decision P-4.

### Z-6. Binding boundary before authorization is decided

No non-placeholder identity may be enabled outside development/test until both this ADR and
SC-1-14's roles and permissions ADR are Accepted and a separate implementation Story has
implemented and verified both boundaries. Accepting authentication alone does not assign
permissions. Until those conditions hold, the application must remain unavailable outside
development/test. In particular, it must not activate the dormant B-01 combinations or expose
cost/personnel data through a caller identity without a resolved permission model.

Source: Issue #150 Z-6; ADR-0005 conditions and B-01 addenda; `docs/architecture/capabilities.md`
rows 179, 192, and 239.

### Z-7. Supplier price data in development/test

Keep the existing prohibition on real supplier price data in development/test databases. This
authentication decision does not relax it. Reconsideration requires a separate human decision
and an appropriate data-protection and environment review.

Source: Issue #150 Z-7; ADR-0005 addendum 2026-09-21, SC-2-03 point 6.

## Conditions routed to this ADR or SC-1-14

The dated 2026-10-01 addendum to ADR-0005 is the complete routing index for authentication- and
role-related closure/reopening conditions in ADR-0004, ADR-0005, and ADR-0013, including all
three B-01 capability entries. No condition is considered satisfied merely because this draft
exists or is later accepted.

## Foundation status

Caller authentication remains `no evidence` in `docs/architecture/capabilities.md`. This ADR
does not change that status. The named-person register also remains subject to all admission
conditions in ADR-0019 §8; accepting this ADR alone does not permit real named-person data.

## Required successor work

- SC-1-14 must decide role dimensions, permission mapping, assignment authority, and the B-01
  combinations routed to it.
- A separate authentication implementation Story, blocked by SC-1-12 and SC-1-14, must implement
  the OIDC contract, safe placeholder lifecycle, realm configuration, secret handling, and
  integration tests.
- Security-auditor review of this draft must be recorded on Issue #150 before gate 1.
- The documentation publishing mechanism must include this decision file.
