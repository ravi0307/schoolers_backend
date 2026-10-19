# Examination Policy Decision Record — Blue Horizon Academy

**Status: PROPOSED — AWAITING EXPLICIT SCHOOL APPROVAL**

This record captures three outstanding examination policies for Blue Horizon
Academy (school id 1), the current Schoolers implementation behavior verified
against the repositories, and the exact changes required **after** the school
approves each decision. Nothing in this document is approved, implemented or
authorized for rollout by virtue of being written down.

- **Document owner (record keeper):** Schoolers engineering — this file only
  records decisions; it does not make them.
- **School:** Blue Horizon Academy (school id 1). Green Valley Public School
  (school id 2) is out of scope; any implementation must remain per-school.
- **Scope:** documentation, approval tracking and readiness only. No
  application code, migrations, seed data, credentials or feature flags were
  changed in producing this revision.
- **Fact vs proposal:** statements prefixed **FACT** were verified in the
  repositories cited (branch state re-verified at revision 2: backend `main`
  ahead of `origin/main` by 4 commits through `783e4dc`, behind 0; frontend
  `main` ahead by 2 commits through `f4cbc5e`, behind 0; both trees clean
  apart from this untracked document; both schools'
  `examinations_enabled = false`). Statements prefixed **PROPOSAL** or
  **QUESTION** are not decisions.
- **Approval evidence:** none has been supplied for any policy. All three
  remain PROPOSED. The approval checklist below is intentionally blank.

Related verification evidence (development environment, flag OFF): focused
backend suites 230/230 passing — including `tests/test_marks_results.py`,
`tests/test_marks_lifecycle.py`, `tests/test_marks_import.py`,
`tests/test_examination_scheduling.py`, `tests/test_correction_audit.py`,
`tests/test_promotion_enrollment.py`, `tests/test_rollout_flag.py`;
frontend contract tests 11/11.

Companion document: `docs/examination-pilot-readiness-checklist.md`
(prerequisites for a future single-school pilot; all items pending).

---

## Policy 1 — Assessment-status treatment

### Proposed decision — NOT APPROVED

> Exclude absent, missing and exempt assessments from obtained marks and
> possible marks, with explicit result-completeness rules.

### Current implementation (verified facts)

- **FACT — Denominators already exclude non-present rows.** Numeric totals are
  computed in `_summarize_marks_rows`
  (`services/marks_service/repository.py:682-702`): only rows with
  `assessment_status == "present"` and a numeric score contribute to
  `obtained` and `possible`; `percentage = obtained / possible` is therefore
  always "over papers sat". Absent, missing and exempt rows contribute
  neither marks nor maximums. The function's own docstring states this rule
  ("Percentages are always over papers sat").
- **FACT — Completeness is a separate flag.** `incomplete` is true when the
  row set is empty **or any row is not `present`** (repository.py:693) —
  so today absent, missing and exempt each make a result incomplete, and
  exempt is not treated as satisfying completion. `subjects_counted`
  (present rows) and `subjects_total` (all rows) expose the gap. Tests pin
  this: `tests/test_marks_results.py:137-147` (absent excluded +
  incomplete), `:285-294` (any non-present row → incomplete; empty = incomplete).
- **FACT — Grade-format exams never show numeric totals.** For
  `result_format == "grade"`, `obtained/possible/percentage/subjects_counted`
  are removed and a `grade_distribution` over present rows is returned
  (repository.py:772-784).
- **FACT — Parent presentation.** `GET /marks/results/student/{id}`
  (flag-gated; `services/marks_service/router.py:383-420`) returns subject
  rows with `assessment_status` plus `obtained/possible/percentage/incomplete`.
  The parent results page renders `obtained/possible · percentage%` with an
  "Incomplete" marker when set, and labels rows "Absent" / "Missing" /
  "Exempt" (`schoolers-web/src/pages/parent/ParentMarks.jsx:14, 107-132`).
  The legacy parent report page (`ParentReport.jsx:66-92`) shows
  presence-style pills ("N absent", "X% present") over its own row set — a
  different presentation, not a marks percentage. Teacher/admin entry grids
  show every workflow state via the staff-only examination "entry" view
  (repository.py:813-850).
- **FACT — Withheld results are hidden from parents everywhere.** Parent
  reads filter `workflow_status == "published" AND is_withheld IS FALSE`
  (`_visible_marks_query`, repository.py:640-645; router.py:63-66 and
  :392-394). Re-verified live with the flag OFF.
- **FACT — "Missing" has two distinct shapes.** (a) a row saved with
  `assessment_status = "missing"`; (b) a class student with **no row at all**,
  reported by `submit_session_marks` as `missing_student_ids`
  (repository.py:353-380) and by the entry view's missing list. Both are
  excluded from totals identically.
- **FACT — Schema guardrails.** `assessment_status IN ('present','absent',
  'missing','exempt')` and non-present rows may carry neither score nor grade
  (`validate_session_payload`, repository.py:244-290; model check
  constraints in `common/models.py`). A zero can never masquerade as an
  absence.

### Alignment with the proposed decision

**FACT (assessment):** the numerical part of the proposed decision —
excluding absent/missing/exempt from obtained **and** possible — is already
the implemented and tested behavior. No denominator change is required;
approval would ratify current behavior and settle the completeness and
display questions below.

### Required changes after approval

Only changes that follow directly from the answers to the questions below
(e.g. redefining `incomplete` for exempt rows, or suppressing percentages
while incomplete) — each would be its own reviewed change with corresponding
test updates. No change is required for the exclusion rule itself.

### Impact of excluding assessments from the denominator (verified behavior)

Percentages are computed over papers sat only. A student with one paper sat
at full marks shows `100%` alongside `incomplete = true` and
`subjects_counted < subjects_total`. The incomplete flag and counted/total
pair are therefore the safeguards against misreading such percentages —
which is why display rules (Question 4) matter to this policy.

### Unresolved questions requiring a school decision

1. **QUESTION — Absent as zero.** Should an absent assessment instead count
   as `0` inside the denominator (the alternative common model)? The
   proposed decision as written excludes absent from both numerator and
   denominator, which is the current behavior. Confirm one model explicitly.
2. **QUESTION — Exempt and completion.** Does an exempt assessment satisfy
   completion requirements? Today any non-present row — including exempt —
   forces `incomplete = true`. If exempt is "finished for the student"
   (waived paper), the flag definition and its tests must change.
3. **QUESTION — Per-status completeness.** Should absent, missing **and**
   exempt each make a result incomplete (current behavior), or only some of
   them (e.g. absent/missing do, exempt does not)? The rule must be stated
   per status.
4. **QUESTION — Display of incomplete results and percentages.** When the
   denominator excludes assessments, may parents/staff still see
   `percentage` (today: yes, with an "Incomplete" marker and
   counted/total), or must percentages be suppressed until complete? Any
   suppression touches the parent results API and both parent UIs.
5. **QUESTION — Missing-row lifecycle.** Should a "missing" row (or an
   unentered student reported by submit) remain indefinitely, or is there a
   resolution deadline? No lifecycle rule exists today.
6. **QUESTION — Legacy term rows.** The same summarizer applies to
   session-less legacy marks (repository.py:785-793). Confirm no separate
   legacy rule is wanted.

---

## Policy 2 — Promotion decisions

### Proposed decision — NOT APPROVED

> Use manual administrator promotion decisions, without automatic pass/fail
> calculations.

### Current implementation (verified facts)

- **FACT — No automatic pass/fail or eligibility calculation exists.** The
  promotion repository (`services/people_service/repository.py:702-876`)
  never reads marks, examinations, attendance or thresholds. Nothing in
  `preview_promotion` or `commit_promotion` can decide that a student has
  passed or failed anything.
- **FACT — Preview is a pure move proposal.** `preview_promotion`
  (repository.py:702-755) maps each active student's current class through
  an administrator-supplied class map: mapped → `suggested_status`
  `"promoted"`; same-class mapping → `"retained"`; unmapped or
  foreign-school target → an **exception** ("assign one at commit");
  students already enrolled in the target year are `already_done`. With no
  source year and no map it produces the founding-year reconciliation roster
  (every active student `enrolled` in their current class). Router docstring:
  "Dry run: proposed moves plus exceptions. Writes nothing"
  (`services/people_service/router.py:289-305`).
- **FACT — Commit is an explicit, audited, idempotent administrative act.**
  `POST /promotions/commit` is admin-only and examinations-flag-gated
  (router.py:307-325). It validates each assignment against the schema's
  status vocabulary (`promoted/retained/enrolled/new` require a target class
  in the same school; `transferred/withdrawn` take none), refuses students
  already enrolled for the target year, writes one `class_enrollments` row
  per student carrying `batch_id` and `decided_by`, and updates the student
  class cache — all in one transaction (repository.py:758-841). Retries with
  the same `(school, idempotency_key)` return the prior receipt unchanged.
- **FACT — Audit trail.** Each commit creates a `promotion_batches` row
  (`created_by`, `status`, `idempotency_key`); batch list/detail endpoints
  expose applied counts and per-student outcomes (router.py:327-360;
  receipt at repository.py:844-859). Enrollment statuses are schema-
  constrained (`class_enrollments` check constraint, `common/models.py`).
- **FACT — UI frames results as proposals.** The admin promotion page
  previews with "review and confirm", toasts "Preview ready: N proposed",
  and confirms with "Promotion committed transactionally"
  (`schoolers-web/src/pages/admin/AdminPromotion.jsx:89-142`). No "passed",
  "failed" or "eligible" wording exists in the promotion UI; "promoted" /
  "retained" appear only as status labels for class moves.

### Alignment with the proposed decision

**FACT (assessment):** manual-only promotion with no automatic pass/fail is
already the implemented behavior end to end (API, repository, UI, tests —
`tests/test_promotion_enrollment.py`).

### Required changes after approval

None required to *enforce* the policy — there is nothing automatic to
remove. Approval should be recorded so future feature requests (e.g.
"auto-promote students above X%") are declined unless this record is amended.

### Recommended (optional) wording changes after approval

- **PROPOSAL — UI disclaimer:** "Promotion is an administrative decision.
  The system does not calculate pass/fail or eligibility." on the promotion
  page.
- **PROPOSAL — Keep proposal ≠ decision** in any future redesign: preview
  output must never be labeled an eligibility result, in UI or audit
  records (batch receipts record who decided what, never why academically).

### Unresolved questions requiring a school decision

1. **QUESTION — Promotion status vocabulary.** Are `promoted / retained /
   enrolled / new / transferred / withdrawn` the school's official outcomes?
   The schema enumerates exactly these; renaming or adding outcomes is a
   schema change.
2. **QUESTION — Founding-year reconciliation.** Is the empty-map mode
   (re-enroll everyone into their current class for a new year) an approved
   school workflow for Blue Horizon, or should it be hidden?
3. **QUESTION — Authorized administrators and exception handling.** Which
   named administrators may preview/commit (endpoints accept any school
   `admin` under the flag)? And how should preview **exceptions** (students
   with no mapped class) be handled operationally — must every exception be
   resolved before commit, or can commit proceed while some students are
   skipped? Today commit simply omits students not present in `assignments`.
4. **QUESTION — Proposal-vs-decision distinction.** Confirm the proposed UI
   disclaimer wording, and confirm that audit records should continue to
   store decisions as class-move statuses only (no academic rationale
   fields exist).

---

## Policy 3 — Published-mark correction approval

### Proposed decision — NOT APPROVED

> Require a different authorized person to approve corrections to published
> marks. Self-approval is not permitted.
>
> **Proposed workflow — NOT APPROVED OR IMPLEMENTED:** teachers can
> initiate and submit correction requests for published marks, but an
> authorized school administrator must independently approve or reject each
> request. The initiating teacher cannot approve their own request.

### Current implementation (verified facts)

- **FACT — Corrections are a single-step, self-approving action today.**
  `POST /marks/{mark_id}/correct` is **admin-only** and flag-gated
  (`services/marks_service/router.py:245-263`). The repository function
  `correct_mark` (`services/marks_service/repository.py:428-457`) requires
  the mark to be published, validates the new values with the same rules as
  entry, then writes one `mark_corrections` row with **both**
  `corrected_by = approved_by = the calling user** and `approved_at = now()`,
  and immediately moves the published mark to the new values. There is no
  pending state, no second actor, no rejection, and no teacher entry point.
- **FACT — The audit API documents the model as self-approval.**
  `GET /marks/{mark_id}/corrections` (admin-only, flag-gated,
  router.py:265-283) returns old/new values, reason, correcting admin, and
  approval timestamp; its docstring states "self-approval model, unchanged".
  `CorrectionRead` exposes `corrected_by/_name/_at` and
  `approved_by/_name/_at` (`services/marks_service/schemas.py:120-132`).
- **FACT — The UI is a one-click correction with a mandatory reason.**
  The admin marks entry page offers "Correct" per published row: value +
  reason form, toast "Correction recorded — open History for the audit
  trail"; History displays "approved by <name> at <time>"
  (`schoolers-web/src/pages/admin/AdminMarksEntry.jsx:112-155, 508-546,
  644-646`). Reason is required client-side and schema-side. No teacher
  correction UI exists.
- **FACT — Tests pin the self-approval behavior.**
  `tests/test_correction_audit.py` asserts a successful correction records
  `corrected_by` and `approved_by` (module docstring lines 3-9; admin
  fixtures at :145-147). Under the proposed policy these assertions must be
  inverted.
- **FACT — Unapproved corrections cannot appear as parent results today —
  because "unapproved" does not exist.** Approval is atomic with creation
  and the corrected value replaces the published mark in place; parent reads
  (published, non-withheld only) therefore always show a fully self-approved
  value. The proposed policy addresses single-person finality, not a leak of
  draft approvals.

### Proposed workflow after approval (PROPOSAL — not implemented)

**Teacher correction request**
- **PROPOSAL —** An authorized teacher selects an eligible published mark
  (a mark they are authorized to grade) and proposes a corrected value with
  a **mandatory reason**.
- **PROPOSAL —** The system records the original value, proposed value,
  teacher identity and submission timestamp; the request enters an explicit
  `pending_approval` state.
- **PROPOSAL —** Submitting must **not** silently alter the published mark
  or any parent-visible result.

**Administrator review**
- **PROPOSAL —** An authorized administrator of the **same school** reviews
  pending requests: original value, proposed value, teacher's reason and
  audit history are visible.
- **PROPOSAL —** The administrator approves or rejects. The initiating
  teacher cannot approve their own request (self-approval prohibited at the
  API and UI level, not merely by convention).
- **PROPOSAL —** Rejection requires a reason; the original published mark
  remains unchanged.
- **PROPOSAL —** Approval records the approving administrator and timestamp
  and applies the approved correction in a safe, auditable transaction
  (re-validated against the live session's maximum and grade scale).

### Implementation changes required after approval (FACT — current code lacks all of these)

1. **Correction states:** `pending_approval`, `approved`, `rejected` on
   `mark_corrections` (e.g. `approval_status` column), with nullable
   rejection reason/timestamp.
2. **Separate API operations:** teacher submission endpoint; administrator
   approve/reject endpoints — distinct from today's single
   `POST /marks/{id}/correct`.
3. **Backend authorization:** teacher submit restricted to authorized
   teachers of the mark's school/subject; administrator review restricted to
   same-school admins; **enforcement that approver ≠ initiator** (403 with a
   distinct message).
4. **Administrator queue:** list endpoint + admin UI surface for pending
   corrections.
5. **Teacher-facing feedback:** request status visibility and rejection
  feedback for the initiating teacher.
6. **Audit history:** submissions, approvals and rejections all recorded
  with actor and timestamp (extend `CorrectionRead` + History UI).
7. **Concurrency / duplicate protection / isolation:** approve and reject
  must be idempotent-safe (double-click or racing admins → one terminal
  transition, second attempt a clear 409); pending corrections are
  school-scoped exactly like today's correction paths.
8. **Database migration + backward compatibility:** add state columns;
   backfill all existing rows as approved self-approved history; API must
   keep serving historic rows (`approved_by` already set).
9. **Tests:** authorization (teacher vs admin vs wrong school vs initiator),
   approve flow, rejection flow with reason, audit integrity, concurrent
  duplicate actions, and **results unchanged before approval** (parent read
  must still show the original value while pending).

### Questions requiring school clarification (answers not assumed)

1. **QUESTION —** Which administrator roles can approve corrections?
2. **QUESTION —** Can a teacher withdraw a pending request?
3. **QUESTION —** Should pending requests expire?
4. **QUESTION —** What happens if no authorized administrator is available?
5. **QUESTION —** Can an administrator return a request for more
   information, or only approve/reject it?
6. **QUESTION —** Does approval update the published result immediately, or
   is a separate republication step required?
7. **QUESTION —** What notifications should teachers receive?
8. **QUESTION —** What retention requirements apply to correction history?

### Note on this revision

**PROPOSAL delta:** revision 1 of this record proposed an admin-initiates +
second-admin-approves model. This revision adopts the school-tasked
**teacher-initiates + administrator-approves** workflow above. The verified
FACTs of current behavior are unchanged.

---

## Approval checklist

One row per policy; each policy is approved, rejected or amended
**independently**. No field below has been completed — no approval evidence
has been supplied. Do not fill in names, dates or signatures without
explicit evidence from the authorized representative.

### Policy 1 — Assessment-status treatment

- **Exact proposed wording (verbatim):** "Exclude absent, missing and exempt
  assessments from obtained marks and possible marks, with explicit
  result-completeness rules."
- **Decision:** ☐ Approved as proposed ☐ Approved with the conditions below
  ☐ Rejected ☐ Changes requested
- **Exact approved wording (if different):** _…_
- **Authorized representative — name / school role:** _…_
- **School:** Blue Horizon Academy
- **Approval date:** _…_
- **Conditions, exceptions, unresolved questions settled or reserved:**
  _…_ (Questions 1–6 above must be answered or explicitly deferred)
- **Required follow-up action:** _…_
- **Approval evidence reference (signed record / ticket / email — required):** _…_
- **Signature:** _…_

### Policy 2 — Promotion decisions

- **Exact proposed wording (verbatim):** "Use manual administrator promotion
  decisions, without automatic pass/fail calculations."
- **Decision:** ☐ Approved as proposed ☐ Approved with the conditions below
  ☐ Rejected ☐ Changes requested
- **Exact approved wording (if different):** _…_
- **Authorized representative — name / school role:** _…_
- **School:** Blue Horizon Academy
- **Approval date:** _…_
- **Conditions, exceptions, unresolved questions settled or reserved:**
  _…_ (Questions 1–4 above)
- **Required follow-up action:** _…_
- **Approval evidence reference (signed record / ticket / email — required):** _…_
- **Signature:** _…_

### Policy 3 — Published-mark correction approval

- **Exact proposed wording (verbatim):** "Require a different authorized
  person to approve corrections to published marks. Self-approval is not
  permitted."
- **Workflow scope explicitly submitted for approval (verbatim):**
  "Teachers can initiate and submit correction requests for published marks,
  but an authorized school administrator must independently approve or
  reject each request. Self-approval is prohibited." — approving this policy
  approves this teacher-request / administrator-approval workflow and the
  prohibition on self-approval, including the requirement that the
  initiating teacher cannot approve their own request.
- **Decision:** ☐ Approved as proposed ☐ Approved with the conditions below
  ☐ Rejected ☐ Changes requested
- **Exact approved wording (if different):** _…_
- **Authorized representative — name / school role:** _…_
- **School:** Blue Horizon Academy
- **Approval date:** _…_
- **Conditions, exceptions, unresolved questions settled or reserved:**
  _…_ (the 8 clarification questions above must be answered or explicitly
  deferred before implementation starts)
- **Required follow-up action:** _…_
- **Approval evidence reference (signed record / ticket / email — required):** _…_
- **Signature:** _…_

**Separation rule:** signing any policy above is **not** permission to
enable the examination feature flag. See the rollout section.

---

## Rollout authorization (separate from policy approval)

**FACT — Policy approval of any item above must not be treated as permission
to enable the examinations feature.** Both schools currently have
`examinations_enabled = false` (re-verified at this revision; per-school
flag with enforced staged rollout per commit `253f850` and
`tests/test_rollout_flag.py`). Enabling the flag is a distinct, separately
authorized operational act.

Prerequisites are tracked in **`docs/examination-pilot-readiness-checklist.md`**
(all items pending). In summary:

1. Production schema state confirmed (examinations, marks-lifecycle and
   flag migrations reviewed/approved — **none may be run without operator
   approval and a `pg_dump` preflight**, per
   `docs/holidays-migration-runbook.md` convention).
2. A verified backup taken and confirmed by an authorized operator.
3. Named, verified administrator and master accounts for the pilot school
   (**operational blocker:** documented development credentials for
   `blue.horizon` and `meera.nair` do not match the live development
   database; credential resets are out of scope for engineering — the school
   must supply or re-provision through an approved process).
4. Only the policies approved above are implemented, tested and reviewed —
   as their own change, never bundled with flag enablement.
5. Explicit, recorded rollout authorization naming the pilot school, the
   approving representative, post-enable verification criteria and rollback
   criteria (rollback = flag off; gated endpoints close, legacy mark entry
   and parent reads remain available — behavior verified locally with the
   flag OFF).

## Change log of this record

| Date | Change |
|---|---|
| (initial) | Revision 1: three proposed policies, verified current behavior, required changes, open questions. Status PROPOSED. |
| (revision 2) | Readiness checkpoint: Policy 3 rewritten to the teacher-request / administrator-approval workflow with self-approval prohibited (full implementation-change list, 8 clarification questions); Policy 1 and 2 questions restated per task specification; per-policy approval checklist added (blank, no evidence supplied); cross-reference to the new pilot-readiness checklist; repository state re-verified (backend ahead 4 / behind 0, frontend ahead 2 / behind 0, flags off). Status remains PROPOSED. |
| (revision 3) | Owner decision 2026-10-19: formal policy approval **deferred for later consideration**. This is a deferral, not an approval — status remains PROPOSED and no approval fields are marked. Policy 3 remains unimplemented; published-mark corrections during the pilot are limited to the existing administrator self-approval path (`POST /marks/{mark_id}/correct` with mandatory reason plus the read-only audit trail), as documented in the pilot-preparation GO/NO-GO report. |
