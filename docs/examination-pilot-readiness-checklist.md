# Examination Pilot-Readiness Checklist — Blue Horizon Academy

**Status: NOT READY — all items pending. Nothing here is authorized.**

Prerequisites for a future single-school examination pilot at Blue Horizon
Academy (school id 1). Companion to
`docs/examination-policy-decision-record.md` (all three policies remain
PROPOSED — AWAITING EXPLICIT SCHOOL APPROVAL). No item below may be marked
done without the stated evidence; engineering must not fabricate approvals,
signatures, credentials or backups.

Legend: ☐ pending · ☑ done (evidence required in the Evidence column).

---

## Checklist

**1. Policy decisions recorded and explicitly approved.**
- ☐
- Evidence: policy record signed per policy (decision, representative name +
  role, date) — approval checklist in the policy record, currently blank.
- Notes: policies may be approved/rejected/amended independently.

**2. Required code changes implemented and reviewed.**
- ☐
- Evidence: approved-scope changes only, each as its own reviewed change;
  never bundled with flag enablement. Policy 3 (teacher request → admin
  approve/reject, self-approval prohibited) is the largest item.
- Notes: no implementation may start before item 1 for that policy.

**3. Relevant backend and frontend tests passing.**
- ☐
- Evidence: focused backend suites (marks, lifecycle, import, scheduling,
  correction audit, promotion, rollout flag) green; frontend contract tests
  green; lint/build clean; test inversions for Policy 3 self-approval
  assertions included.
- Notes: local development E2E evidence exists (see appendix) but does not
  satisfy this item — Policy 3 test inversions are impossible until that
  policy is approved and implemented.

**4. Authorized administrator/master accounts verified without resetting
credentials.**
- ☐
- Evidence: successful login checks by an authorized operator.
- Notes: operational blocker — documented dev credentials for
  `blue.horizon` / `meera.nair` do not match the live development DB;
  credential resets are out of scope for engineering. The school must
  supply or re-provision through an approved process.

**5. Production database backup created and verified by an authorized
operator.**
- ☐
- Evidence: `pg_dump` (or platform equivalent) completed and restore-checked
  by the operator; timestamp recorded.
- Notes: follow `docs/holidays-migration-runbook.md` conventions; no
  migration runs without this.

**6. Required examination and marks-lifecycle migrations reviewed and
approved.**
- ☐
- Evidence: migration list reviewed against production schema state
  (examinations tables, marks-lifecycle DDL, feature-flag column/migration,
  plus any approved-Policy-3 state columns); operator approval recorded.
- Notes: blocked on items 1 (relevant policies) and 5 (backup).

**7. Rollback procedure documented and tested where practical.**
- ☐
- Evidence: written rollback = flag off (gated endpoints close; legacy mark
  entry and parent reads remain available); migration rollback notes;
  local verification with flag OFF recorded (already demonstrated
  development-side: `examinations_enabled = f` both schools, legacy flows
  working).

**8. The specific pilot school identified.**
- ☐
- Evidence: named in the rollout authorization — proposed: Blue Horizon
  Academy (school id 1). Must be re-confirmed in the authorization itself.

**9. Separate explicit authorization obtained for enabling that school's
examination feature flag.**
- ☐
- Evidence: recorded authorization naming the school, approving
  representative and date — distinct from any policy approval (policy
  approval ≠ flag enablement).
- Notes: flag is per-school; Green Valley must remain off.

**10. Post-enable verification and rollback criteria documented.**
- ☐
- Evidence: written checks to run immediately after enablement (admin login,
  exam/session/mark read, parent result read, correction flow per item 11)
  and explicit numeric/go-no-go triggers for rolling back.

**11. Teacher correction submission and administrator approval verified
end-to-end before relying on corrections.**
- ☐
- Evidence: one full live run — teacher submits a correction request
  (mandatory reason, pending state, published mark unchanged), a different
  same-school admin reviews and approves (approver ≠ initiator enforced) and
  a second path rejected (reason required, original preserved); audit
  history shows both.
- Notes: only possible after items 1 (Policy 3) and 2 (its implementation).
- Required verification: This end-to-end verification is required after
  Policy 3 has been approved and its implementation completed. It must
  demonstrate that pending corrections do not change published marks,
  approvals require a different authorized administrator, and rejections
  preserve the original mark. Audit history must record both outcomes.

---

## Gate summary

- Enabling `examinations_enabled` for the pilot school requires items 1–10
  (and 11 before relying on corrections operationally), plus the separate
  rollout authorization described in the policy record's rollout section.
- Current flag state (re-verified): Blue Horizon = false, Green Valley =
  false. Must stay false until item 9 is satisfied.

## Appendix — local verification evidence (development only)

**This appendix records evidence. It completes no checklist item.** All runs
below used only the isolated local clone `schoolersdb_e2e` (school 1 flag ON
in the clone only). Source `schoolersdb` flags were re-verified `false` for
both schools before and after every run. This is **not** production
verification, **not** policy approval, and **not** rollout authorization.

- **Full local E2E suite** — `schoolers-web/tests/e2e-examinations.mjs`
  (Playwright, admin/teacher/parent journeys + isolation API probes):
  **45/45 passed** (boot 5, admin 22, teacher 5, parent 6, isolation 7).
  Artifacts: `…/T/opencode/e2e/results.json` and journey screenshots under
  `…/T/opencode/e2e/shots/`.
- **Focused gap suite** — `schoolers-web/tests/e2e-examinations-gaps.mjs`:
  **4/4 passed**, independently re-run. Verifies (1) CSV template download —
  filename `marks-session-<id>-template.csv`, headers equal
  `TEMPLATE_COLUMNS`, one row per active class student, byte-equal to the
  template API response; (2) unauthorized teacher promotion preview and
  commit both return HTTP 403 with an authorization-denial detail while
  `promotion_batches` and `class_enrollments` counts stay unchanged;
  (3) parent report-card marks, maximums, percentages and grade letters
  computed independently from database fixture sums and the school's pinned
  `grade_scale` bands, cross-checked against the results API, including the
  no-band case (percentage below the lowest band renders no grade letter).
  Artifacts: `…/T/opencode/e2e/gap-results.json` and screenshots
  `gap-02-template-downloaded.png`, `gap-03-parent-grade-percentage.png`
  (two `*-FAIL.png` files are retained from intermediate iterations of the
  same script, before its final green run).

## Change log

| Date | Change |
|---|---|
| (initial) | Created at readiness checkpoint (revision 2 of the policy record). All items pending; no evidence supplied. |
| (2026-10-19) | Appendix added: verified local development E2E evidence (45/45 main suite + 4/4 focused gap suite, re-run) with artifact paths and explicit disclaimers. No checklist item marked; status unchanged (NOT READY). |
