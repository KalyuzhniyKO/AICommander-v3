from typing import Literal

from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field

from .schema import ManualError
from .service import ManualChatGPT
from .source import SourceError


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,hide_input_in_errors=True)


class Check(Model):force:bool=False


class Selection(Model):
    selection_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    task_id:int=Field(gt=0)
    model_label:str=Field(min_length=1,max_length=80)
    effort:Literal['Instant','Medium','High','Extra High','Pro']='High'
    plan:Literal['unknown','free','go','plus','pro','business','enterprise','edu']='unknown'
    model_available:bool=False
    effort_available:bool=False
    high_available:bool|None=None
    announced_available:bool|None=None
    latest_visible_confirmed:bool=False
    confirmed_picker:bool=False


class Export(Model):
    export_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    task_id:int=Field(gt=0)
    selection_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    reviewed_context:str=Field(min_length=1,max_length=16384)
    confirm_sanitized:bool=False
    language:Literal["ru","en"]="ru"


class Answer(Model):
    import_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    export_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    answer:str=Field(min_length=1,max_length=16384)
    confirm_origin:bool=False


def create_manual_router(repository_dependency):
    router=APIRouter(prefix='/manual-chatgpt',tags=['manual-chatgpt'])
    def invoke(repo,method,*args):
        try:return getattr(ManualChatGPT(repo.conn),method)(*args)
        except SourceError:
            raise HTTPException(409,detail={'code':'public_source_unavailable','message':'Публичный источник не подтверждён.'}) from None
        except ManualError as error:
            raise HTTPException(409,detail={'code':error.code,'message':'Ручной шаг не подтверждён; проверьте источник, селектор и выбранную задачу.'}) from None
    @router.get('/status')
    def status(task_id:int|None=None,repo=Depends(repository_dependency)):
        return invoke(repo,'status',task_id)
    @router.post('/check-source')
    def check(payload:Check,repo=Depends(repository_dependency)):
        return invoke(repo,'check_source',payload.force)
    @router.post('/selections')
    def select(payload:Selection,request:Request,repo=Depends(repository_dependency)):
        return invoke(repo,'confirm',payload.task_id,payload.model_dump(),request.state.owner)
    @router.get('/tasks/{task_id}/context')
    def draft(task_id:int,repo=Depends(repository_dependency)):
        return invoke(repo,'draft',task_id)
    @router.post('/exports')
    def export(payload:Export,request:Request,repo=Depends(repository_dependency)):
        return invoke(repo,'export',payload.model_dump(),request.state.owner)
    @router.post('/answers')
    def answer(payload:Answer,request:Request,repo=Depends(repository_dependency)):
        return invoke(repo,'import_answer',payload.model_dump(),request.state.owner)
    return router
