"""Owner-only spending visibility/approval; never starts inference."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest
from pydantic import Field, model_validator

from .models import Model
from .runtime import RuntimeSpend
from .ledger import BudgetError


class Approval(Model):
    approval_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    scope: Literal['global','project','task']
    task_id: int | None = Field(default=None, gt=0)
    amount_micro_usd: int = Field(ge=0, le=10**12)
    allow_paid: bool = False
    reference: str | None = Field(default=None, max_length=128)
    expected_revision: int = Field(ge=0)
    expected_policy_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: bool

    @model_validator(mode='after')
    def explicit_confirmation(self):
        if self.confirm is not True:
            raise ValueError('Owner confirmation required')
        return self


def create_spending_router(repository_dependency, settings_getter):
    router = APIRouter(prefix='/spending',tags=['owner-spending'])

    def invoke(repo, method, *args, **kwargs):
        try:
            return getattr(RuntimeSpend(repo.conn,settings_getter()),method)(*args,**kwargs)
        except BudgetError as error:
            raise HTTPException(409,detail={'code':error.code,'message':'Расходы заблокированы серверной политикой; проверьте бюджет, тарифы и неизвестные операции.'}) from None

    @router.get('/status')
    def status(repo=Depends(repository_dependency)):
        return invoke(repo,'status')

    @router.get('/tasks/{task_id}')
    def task_status(task_id:int,run_id:int|None=None,mode:Literal["mock","hermes"]="mock",repo=Depends(repository_dependency)):
        return invoke(repo,'status',task_id,run_id,mode)

    @router.post('/approvals')
    def approve(payload:Approval,request:HttpRequest,repo=Depends(repository_dependency)):
        values = payload.model_dump(exclude={'confirm'})
        values['actor'] = request.state.owner
        return invoke(repo,'approve',**values)

    return router
