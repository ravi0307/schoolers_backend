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
import repository as repo
from schemas import (
    FeeCreate, FeeEntry, FeeSheet, SalaryCreate, SalaryEntry, SalarySheet,
)

router = APIRouter(tags=["accounts"])


@router.get("/accounts/salaries", response_model=SalarySheet)
def salary_sheet(
    months: int = Query(repo.DEFAULT_MONTHS, ge=1, le=24),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.salary_sheet(db, school_id, repo.recent_months(months))


@router.get("/accounts/fees", response_model=FeeSheet)
def fee_sheet(
    months: int = Query(repo.DEFAULT_MONTHS, ge=1, le=24),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.fee_sheet(db, school_id, repo.recent_months(months))


@router.post("/accounts/salaries", response_model=SalaryEntry, status_code=201)
def record_salary(
    payload: SalaryCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.record_salary(db, school_id, payload.model_dump())


@router.post("/accounts/fees", response_model=FeeEntry, status_code=201)
def record_fee(
    payload: FeeCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.record_fee(db, school_id, payload.model_dump())


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
