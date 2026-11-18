import copy
from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.config import ROOT, META
from app.engine import evaluate
from app.errors import BusinessError
from app import store, main

templates = Jinja2Templates(directory=str(ROOT / 'app/templates'))


def render(request: Request, key):
    run_id = request.query_params.get('run_id')
    if run_id:
        state = store.get(main.DB_PATH, run_id)
        needs_full = key in ('certificate', 'archive')
        if needs_full and ('balance' not in state or 'certificate_draft' not in state):
            # 证书与归档页需要完整流水线与证书草稿；按该批不可变快照补算，不改写已存批次。
            snapshot = state.get('input_snapshot')
            if snapshot:
                state = evaluate({'dataset': state.get('source', 'dirty')}, snapshot=snapshot)
            else:
                state = copy.deepcopy(state)
                from app.modules.certificate.rules import apply as certificate_view
                certificate_view(state)
        elif key not in state.get('views', {}):
            state = copy.deepcopy(state)
            from app.modules.certificate.rules import apply as certificate_view
            certificate_view(state)
            state['views'][key] = state['views'].get(key, {'title': '', 'rows': [], 'metrics': {}})
    else:
        state = store.save(main.DB_PATH, evaluate({'dataset': request.query_params.get('dataset', 'dirty')}))
    context = {'meta': META, 'key': key, 'state': state, 'view': state['views'][key]}
    if key in ('certificate', 'archive'):
        context['certificate'] = store.certificate_overview(main.DB_PATH, state)
    if key == 'archive':
        try:
            context['formal_archive'] = store.archive_overview(main.DB_PATH, state['manifest']['run_id'])
        except BusinessError as error:
            if error.code != 'NOT_FOUND':
                raise
            context['formal_archive'] = None
    return templates.TemplateResponse(request=request, name=f'{key}.html', context=context)
