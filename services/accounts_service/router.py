"""Accounts routes: staff salary and student fee sheets.

Read endpoints are open to the roles that need to see a school's money
(admin, plus the school itself via its own portal is not needed here, so
admin only), and every write is admin-only. `require_school_scope` supplies
the school id, so nothing in these handlers trusts a school id from the
request body.
"""
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import ForbiddenError
from common.models import Parent, ParentStudent, Student
import repository as repo
from schemas import (
    FeeCreate, FeeDeposit, FeeDepositEntry, FeeEntry, FeePlan, FeePlans, FeeSheet,
    SalaryCreate, SalaryEntry, SalarySheet,
)

router = APIRouter(tags=["accounts"])


@router.get("/accounts/salaries", response_model=SalarySheet)
def salary_sheet(
    months: int = Query(repo.DEFAULT_MONTHS, ge=1, le=24),
    end: str | None = Query(
        None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Last month of the window, YYYY-MM. Defaults to the current month.",
    ),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.salary_sheet(db, school_id, repo.month_window(months, end))


@router.get("/accounts/fees", response_model=FeeSheet)
def fee_sheet(
    months: int = Query(repo.DEFAULT_MONTHS, ge=1, le=24),
    end: str | None = Query(
        None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Last month of the window, YYYY-MM. Defaults to the current month.",
    ),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.fee_sheet(db, school_id, repo.month_window(months, end))


@router.get("/accounts/fees/student/{student_id}", response_model=list[FeeEntry])
def student_fee_history(
    student_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "admin")),
):
    """Return deposited fees for one child, after verifying parent ownership."""
    if current_user.role == "parent":
        linked = (
            db.query(ParentStudent.id)
            .join(Parent, Parent.parent_id == ParentStudent.parent_id)
            .join(Student, Student.student_id == ParentStudent.student_id)
            .filter(
                ParentStudent.parent_id == current_user.linked_person_id,
                Parent.school_id == school_id,
                Parent.is_active.is_(True),
                ParentStudent.student_id == student_id,
                Student.school_id == school_id,
            )
            .first()
        )
        if not linked:
            raise ForbiddenError("You can only view fee history for your own children")
    return repo.student_fee_history(db, school_id, student_id)


@router.post("/accounts/salaries", response_model=SalaryEntry, status_code=201)
def record_salary(
    payload: SalaryCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    # exclude_unset: a field the caller did not send is left alone, so a
    # correction of the figure cannot delete the remark beside it. Clearing
    # a remark is explicit (note: null).
    return repo.record_salary(db, school_id, payload.model_dump(exclude_unset=True))


@router.post("/accounts/fees", response_model=FeeEntry, status_code=201)
def record_fee(
    payload: FeeCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.record_fee(db, school_id, payload.model_dump(exclude_unset=True))


@router.delete("/accounts/salaries/{staff_id}/{month}", status_code=204)
def clear_salary(
    staff_id: int,
    month: str,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    repo.delete_salary(db, school_id, staff_id, month)
    return Response(status_code=204)


@router.get("/accounts/fees/plans", response_model=FeePlans)
def fee_plans(
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """The deposit periods on offer, so the client never hardcodes them.

    The month counts are the same table the service splits with, so a plan
    offered in the dialog is by construction a plan the server can honour.
    """
    return {
        "plans": [
            {"plan": key, "months": repo.FEE_PLANS[key], "label": repo.FEE_PLAN_LABELS[key]}
            for key in repo.FEE_PLANS
        ]
    }


@router.post("/accounts/fees/deposit/preview", response_model=FeeDepositEntry)
def preview_fee_deposit(
    payload: FeeDeposit,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """What a deposit would write, without writing it.

    A yearly deposit touches twelve months of a fee register, so the months
    and the split have to be shown before the admin commits, not discovered
    afterwards.
    """
    return repo.plan_fee_deposit(db, school_id, payload.model_dump(exclude_unset=True))


@router.post("/accounts/fees/deposit", response_model=FeeDepositEntry, status_code=201)
def deposit_fee(
    payload: FeeDeposit,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.deposit_fee(db, school_id, payload.model_dump(exclude_unset=True))


@router.delete("/accounts/fees/{student_id}/{month}", status_code=204)
def clear_fee(
    student_id: int,
    month: str,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    repo.delete_fee(db, school_id, student_id, month)
    return Response(status_code=204)
