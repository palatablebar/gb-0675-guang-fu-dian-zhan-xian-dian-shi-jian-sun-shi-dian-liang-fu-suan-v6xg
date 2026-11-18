from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
from app import main, store
from app.errors import BusinessError
router = APIRouter()


@router.get('/certificate')
def page(request: Request):
    return render(request, 'certificate')


@router.post('/api/certificate')
def calculate(payload: dict = Body(default={})):
    return evaluate(payload)


@router.get('/api/certificate/runs/{run_id}')
def overview(run_id: str):
    return store.certificate_overview(main.DB_PATH, store.get(main.DB_PATH, run_id))


@router.post('/api/certificate/issue')
def issue(payload: dict = Body(default={})):
    run_id = payload.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        raise BusinessError('BAD_RUN', '缺少批次号')
    mode = payload.get('mode', 'bounded')
    note = payload.get('note', '')
    if not isinstance(note, str):
        raise BusinessError('BAD_NOTE', '备注须为文本')
    state = store.get(main.DB_PATH, run_id)
    return store.issue_certificate(main.DB_PATH, state, run_id, mode=mode, note=note)
