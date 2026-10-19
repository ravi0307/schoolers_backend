# Examination Pilot Action Register — Blue Horizon Academy

**Status: OPEN — no action completed. Nothing here authorizes a pilot or
feature-flag enablement.**

Operational companion to:
- `docs/examination-policy-decision-record.md` (PROPOSED — AWAITING EXPLICIT
  SCHOOL APPROVAL; Policies 1–3 all unsigned),
- `docs/examination-pilot-readiness-checklist.md` (NOT READY — all 11 items
  pending; local E2E evidence appendix completes no item).

Legend: ☐ pending · ☑ complete (**only** when both the required evidence and
the named authorized actor are recorded in the row).

---

## Actions

| # | Action | Owner | Status | Required evidence | Current state / references |
|---|---|---|---|---|---|
| 1 | Provision and verify administrator/master accounts for the pilot school **without credential resets** | School (authorized operator) | ☐ | Successful login checks recorded by the operator, naming accounts verified | **Blocked:** documented development credentials for `blue.horizon` / `meera.nair` do not match the live development database. Credential resets are out of scope for engineering — the school must supply or re-provision through an approved process (readiness checklist item 4). |
| 2 | Take a production database backup and verify restore | Authorized operator | ☐ | `pg_dump` (or platform equivalent) completed **and restore-checked**, timestamp recorded | None. Follow `docs/holidays-migration-runbook.md` conventions. No migration may run without this (checklist item 5). |
| 3 | Review and approve required migrations against production schema state | Operator + engineering | ☐ | Reviewed migration list + operator approval recorded | Current feature code requires **no new migrations** (schema via dump/`create_all`; Phase-6 finding). Production schema-state review is still owed; any future Policy 3 state columns are blocked on policy approval (checklist item 6). |
| 4 | Document the rollback procedure and verify flag-off preserves legacy workflows | Engineering documents; operator approves | ☐ | Written rollback runbook (flag off → gated endpoints close; legacy mark entry and parent reads remain available) + recorded verification | Partial **local** evidence only: flag-OFF behavior verified in development (`examinations_enabled = f` for both schools before/after every E2E run; isolation probe I4 shows legacy reads 200 while examinations 403). Production runbook not yet written (checklist item 7). |
| 5 | Define post-enable smoke tests, monitoring, and rollback triggers | Engineering + operator | ☐ | Written criteria: immediate checks after enablement (admin login, exam/session/mark read, parent result read, correction flow) and explicit numeric/go-no-go rollback triggers | None (checklist item 10). |
| 6 | Confirm the specific pilot school | School decision-maker | ☐ | Named in the recorded rollout authorization | Proposed: Blue Horizon Academy (school id 1). Not confirmed. Green Valley Public School (school id 2) must remain off (checklist item 8). |
| 7 | Separate, explicit authorization to enable that school's examination feature flag | Authorized representative (distinct from policy signers where governance requires) | ☐ | Recorded authorization naming the school, approving representative, date, post-enable verification criteria and rollback criteria | None. **Policy approval of any of Policies 1–3 is not this authorization** (policy record, rollout section; checklist item 9). |

## Dependencies and hard gates

- **Policy 1, 2, 3 approvals** (decision record approval checklist — blank,
  including the required approval-evidence reference per policy) must be
  recorded before any code changes for those policies start.
- **Policy 3 implementation is blocked until Policy 3 is documented as
  approved.** When approved, it ships as its own reviewed change —
  pending/approved/rejected states, published marks unchanged while pending,
  independent same-school administrator approval (approver ≠ initiator),
  full audit history, and authorization/concurrency/idempotency/school-
  isolation tests — **never bundled with feature-flag enablement**. Its live
  dual-path verification is readiness-checklist item 11.
- **Flag enablement** requires checklist items 1–10 complete **and** action 7
  recorded. Flag activation is a separate operational act from all testing
  and all policy approvals.

## Current flag state (last verified 2026-10-19)

Blue Horizon (school 1) = `false`, Green Valley (school 2) = `false` in the
source database. Must stay `false` until action 7 is complete. The isolated
development clone `schoolersdb_e2e` (school 1 `true`) exists solely for
local E2E verification and has no bearing on production settings.

## Change log

| Date | Change |
|---|---|
| (2026-10-19) | Created during release preparation. All 7 actions pending; no evidence fabricated; no checklist or approval field completed. |
