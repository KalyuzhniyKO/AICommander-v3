"""Real planning contract over the same lifecycle/tables, separate from frozen v1."""
from fastapi import APIRouter, Depends, HTTPException

from backend.app.budgets.runtime import RuntimeSpend
from backend.app.storage.repositories import Repository
from .hermes import HermesDirector
from .models import HermesCreateRun, HermesRunSnapshot, Command, OwnerDecision, OperationSnapshot
from .service import Taskflow, FlowError


def create_hermes_router(repository_dependency, settings_getter):
    router=APIRouter(prefix='/taskflow/v2',tags=['hermes-planning'])

    def director(repo=None):
        settings=settings_getter()
        agent = HermesDirector(settings.hermes,settings.omniroute)
        if repo is not None:agent.spending = RuntimeSpend(repo.conn,settings)
        return agent

    def invoke(repo, method, *args):
        agent=director(repo)
        if method=='create' and not agent.available:
            raise HTTPException(503,detail={'code':'hermes_unavailable','message':'Hermes transport не включён или не настроен на сервере'})
        try:return getattr(Taskflow(repo.conn,agent,mode='hermes'),method)(*args)
        except FlowError as exc:
            raise HTTPException(exc.status_code,detail={'code':exc.code,'message':str(exc)}) from None

    @router.get('/capabilities')
    def capabilities():
        agent=director()
        guarded=callable(getattr(agent,'reserve_budget',None)) and callable(getattr(agent,'finish_budget',None))
        return {'configured':agent.available,'director':'hermes','scope':'real_planning_artifacts_only',
                'runtime_budget_guarded':guarded,'owner_controls_version':1,
                'code_executed':False,'qa_verified':False}

    @router.post('/tasks/{task_id}/runs',response_model=HermesRunSnapshot)
    def create(task_id:int,payload:HermesCreateRun,repo:Repository=Depends(repository_dependency)):
        return invoke(repo,'create',task_id,payload)

    @router.get('/tasks/{task_id}/runs',response_model=list[HermesRunSnapshot])
    def list_runs(task_id:int,repo:Repository=Depends(repository_dependency)):
        return invoke(repo,'list_for_task',task_id)

    @router.get('/runs/{run_id}',response_model=HermesRunSnapshot)
    def get(run_id:int,repo:Repository=Depends(repository_dependency)):
        return invoke(repo,'get',run_id)

    @router.get('/runs/{run_id}/operations',response_model=list[OperationSnapshot])
    def operations(run_id:int,repo:Repository=Depends(repository_dependency)):
        return invoke(repo,'list_operations',run_id)

    @router.post('/runs/{run_id}/commands',response_model=HermesRunSnapshot)
    def command(run_id:int,payload:Command,repo:Repository=Depends(repository_dependency)):
        return invoke(repo,'apply',run_id,payload)

    @router.post('/runs/{run_id}/owner-decision',response_model=HermesRunSnapshot)
    def decision(run_id:int,payload:OwnerDecision,repo:Repository=Depends(repository_dependency)):
        return invoke(repo,'apply',run_id,payload)

    return router
