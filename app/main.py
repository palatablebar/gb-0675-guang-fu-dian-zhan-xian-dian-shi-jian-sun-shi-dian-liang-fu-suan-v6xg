from contextlib import asynccontextmanager
from importlib import import_module
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, META, MODULES, DB_PATH
from app.errors import BusinessError
from app.engine import evaluate
from app import store
from app.archive import serialize, serialize_formal


@asynccontextmanager
async def lifespan(app):
    store.save(DB_PATH, evaluate())
    yield


app = FastAPI(title=META['title'], lifespan=lifespan)
app.mount('/static', StaticFiles(directory=str(ROOT / 'app/static')), name='static')


@app.exception_handler(BusinessError)
async def business_error(request: Request, error: BusinessError):
    return JSONResponse({'code': error.code, 'detail': error.message}, status_code=error.status)


@app.get('/')
def home():
    return RedirectResponse('/' + MODULES[0], status_code=303)


@app.get('/healthz')
def health():
    return {'status': 'ok', 'project': META['short']}


@app.post('/api/runs')
def create(payload: dict = Body(default={})):
    # 显式导入才视为新上游采信，可能推进作用域并使旧证书失效。
    return store.save(DB_PATH, evaluate(payload), advance_scope=True)


@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id: str):
    previous = store.get(DB_PATH, run_id)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING', '旧批缺少源快照，请重新导入')
    # 重跑同一快照：只复算并核验，不覆盖已确认的证书与归档；只有重跑当前采信批次才可能推进作用域。
    return store.save(DB_PATH, evaluate({'dataset': previous['source']}, snapshot=previous['input_snapshot']),
                      advance_scope='auto')


@app.get('/api/runs')
def runs():
    return store.history(DB_PATH)


@app.get('/api/runs/{run_id}')
def run(run_id: str):
    return store.get(DB_PATH, run_id)


@app.post('/api/runs/{run_id}/review')
def review(run_id: str, payload: dict = Body(...)):
    revision = payload.get('revision')
    if not isinstance(revision, int) or revision < 1:
        raise BusinessError('BAD_REVISION', '版本号须为正整数')
    return store.review(DB_PATH, run_id, revision)


@app.post('/api/runs/{run_id}/seal')
def seal(run_id: str):
    # 正式归档：必须证据闭合、有当前有效且闭合的证书，缺一不可。
    state = store.get(DB_PATH, run_id)
    return store.seal_archive(DB_PATH, state, run_id)


@app.get('/api/archive/{run_id}')
def formal_archive(run_id: str):
    return store.get_archive(DB_PATH, run_id)


@app.get('/api/export/{run_id}')
def export(run_id: str):
    result = store.get(DB_PATH, run_id)
    try:
        formal = store.get_archive(DB_PATH, run_id)
    except BusinessError as error:
        if error.code != 'NOT_FOUND':
            raise
        formal = None
    if formal:
        body = serialize_formal(result, formal)
    else:
        body = serialize(result)
    return Response(body, media_type='application/zip',
                    headers={'Content-Disposition': f'attachment; filename="{run_id}.zip"'})


for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}.handler').router)
