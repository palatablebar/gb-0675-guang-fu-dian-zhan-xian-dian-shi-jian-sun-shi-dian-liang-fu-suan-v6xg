import sqlite3, json, hashlib
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError
from app.certificate import (build_basis, build_certificate, build_issued,
                             invalidation_reason)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')",
    "CREATE TABLE IF NOT EXISTS certificates (seq INTEGER PRIMARY KEY AUTOINCREMENT, certificate_id TEXT UNIQUE NOT NULL, scope_id TEXT NOT NULL, version INTEGER NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, invalidated_at TEXT, reason TEXT, UNIQUE(scope_id, version))",
    "CREATE UNIQUE INDEX IF NOT EXISTS cert_one_active ON certificates(scope_id) WHERE state='已签发'",
    "CREATE TABLE IF NOT EXISTS scope_state (scope_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run_id TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS formal_archives (run_id TEXT PRIMARY KEY, archive_id TEXT UNIQUE NOT NULL, scope_id TEXT NOT NULL, certificate_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
)


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    for statement in SCHEMA:
        con.execute(statement)
    return con


def _now(now=None):
    return now or datetime.now(timezone.utc).isoformat()


def _rollback(con):
    try:
        con.execute('ROLLBACK')
    except sqlite3.OperationalError:
        pass


def save(path, result, advance_scope=False):
    """落库批次。

    advance_scope=True：显式导入，视为新的上游采信结论。
    'auto'：重跑当前采信批次才推进；重放旧批不把作用域倒退。
    """
    run_id = result['manifest']['run_id']
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT result FROM runs WHERE id=?', (run_id,)).fetchone()
        if existing is None:
            con.execute('INSERT INTO runs(id,digest,result) VALUES(?,?,?)',
                        (run_id, result['manifest']['sha256'], json.dumps(result, ensure_ascii=False)))
        elif len(result.get('views', {})) > len(json.loads(existing['result']).get('views', {})):
            # 批号由输入决定，各模块入口会算出同一批号；只允许更完整的结果升级，
            # 避免先跑的局部结果把完整结果挡在库外。
            con.execute('UPDATE runs SET result=? WHERE id=?',
                        (json.dumps(result, ensure_ascii=False), run_id))
        advance = advance_scope
        if advance_scope == 'auto':
            scope_id = build_basis(result)['scope_id']
            row = con.execute('SELECT run_id FROM scope_state WHERE scope_id=?', (scope_id,)).fetchone()
            advance = row is None or row['run_id'] == run_id
        if advance:
            advance_scope_evidence(con, result)
        con.execute('COMMIT')
    except Exception:
        _rollback(con)
        raise
    finally:
        con.close()
    return get(path, run_id)


def get(path, run_id):
    con = connect(path)
    try:
        row = con.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise BusinessError('NOT_FOUND', '批次不存在', 404)
    result = json.loads(row['result'])
    result.update(revision=row['revision'], status=row['status'])
    return result


def history(path):
    con = connect(path)
    try:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]
    finally:
        con.close()


def review(path, run_id, revision):
    con = connect(path)
    try:
        cur = con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',
                          ('已复核', run_id, revision))
        if cur.rowcount != 1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():
                raise BusinessError('NOT_FOUND', '批次不存在', 404)
            raise BusinessError('STALE_VERSION', '复核版本已更新，请刷新', 409)
    finally:
        con.close()
    return get(path, run_id)


# ---------------------------------------------------------------- 证据作用域

def _fingerprint_row(con, scope_id):
    row = con.execute('SELECT fingerprint FROM scope_state WHERE scope_id=?', (scope_id,)).fetchone()
    return row['fingerprint'] if row else None


def scope_fingerprint(path, scope_id):
    con = connect(path)
    try:
        return _fingerprint_row(con, scope_id)
    finally:
        con.close()


def scope_run(path, scope_id):
    """当前采信批次；页面默认展示它，避免翻看旧批时误以为仍可出证。"""
    con = connect(path)
    try:
        row = con.execute('SELECT run_id FROM scope_state WHERE scope_id=?', (scope_id,)).fetchone()
    finally:
        con.close()
    return row['run_id'] if row else None


def advance_scope_evidence(con, result):
    """新上游证据进入时推进作用域指纹；指纹变化即刻作废旧证书，等待重判。"""
    basis = build_basis(result)
    now = _now()
    previous = con.execute('SELECT fingerprint FROM scope_state WHERE scope_id=?',
                           (basis['scope_id'],)).fetchone()
    if previous and previous['fingerprint'] == basis['fingerprint']:
        return
    con.execute('INSERT INTO scope_state(scope_id,fingerprint,run_id,updated_at) VALUES(?,?,?,?) '
                'ON CONFLICT(scope_id) DO UPDATE SET fingerprint=excluded.fingerprint,'
                'run_id=excluded.run_id,updated_at=excluded.updated_at',
                (basis['scope_id'], basis['fingerprint'], result['manifest']['run_id'], now))
    previous_basis = None
    if previous:
        latest = con.execute(
            "SELECT payload FROM certificates WHERE scope_id=? ORDER BY version DESC LIMIT 1",
            (basis['scope_id'],)).fetchone()
        if latest:
            previous_basis = json.loads(latest['payload']).get('basis')
    reason = invalidation_reason(previous_basis, basis) if previous_basis else '上游采信结果变化'
    con.execute("UPDATE certificates SET state='已失效', invalidated_at=?, reason=? "
                "WHERE scope_id=? AND state='已签发'",
                (now, f'上游证据变化：{reason}', basis['scope_id']))


def invalidate_scope(path, scope_id, reason, now=None):
    now = _now(now)
    con = connect(path)
    try:
        rows = con.execute("SELECT seq FROM certificates WHERE scope_id=? AND state='已签发'",
                           (scope_id,)).fetchall()
        for row in rows:
            con.execute("UPDATE certificates SET state='已失效', invalidated_at=?, reason=? WHERE seq=?",
                        (now, reason, row['seq']))
    finally:
        con.close()
    return len(rows)


# ------------------------------------------------------------------ 证书签发

def _active_certificate(con, scope_id):
    return con.execute("SELECT * FROM certificates WHERE scope_id=? AND state='已签发'",
                       (scope_id,)).fetchone()


def _next_version(con, scope_id):
    row = con.execute('SELECT MAX(version) AS version FROM certificates WHERE scope_id=?',
                      (scope_id,)).fetchone()
    return (row['version'] or 0) + 1


def _write_version(con, issued, fingerprint, now):
    """独立写入步骤，便于验证签发失败时的整体回滚。"""
    con.execute('INSERT INTO certificates(certificate_id,scope_id,version,fingerprint,state,payload,created_at) '
                'VALUES(?,?,?,?,?,?,?)',
                (issued['certificate_id'], issued['scope_id'], issued['version'], fingerprint,
                 '已签发', json.dumps(issued, ensure_ascii=False), now))


def issue_certificate(path, state, run_id, mode='bounded', note='', now=None):
    if mode not in {'bounded', 'formal'}:
        raise BusinessError('BAD_MODE', '签发模式须为 bounded 或 formal')
    if 'balance' not in state or 'energy_rows' not in state:
        raise BusinessError('INCOMPLETE_RUN', '批次尚未跑到损失核算与对账，不能出证；请先完成复算')
    basis = build_basis(state)
    draft = build_certificate(state, basis)
    if not draft['devices']:
        raise BusinessError('NO_TARGETS', '该批次没有限电目标设备，无法签发')
    if mode == 'formal' and not draft['closed']:
        raise BusinessError('EVIDENCE_OPEN', '证据未闭合，不能正式签发；请使用限定签发或等待证据齐备')
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        current = _fingerprint_row(con, basis['scope_id'])
        if current is None:
            # 首次签发即以当前证据作为作用域基线，后续上游变化才能被识别。
            con.execute('INSERT INTO scope_state(scope_id,fingerprint,run_id,updated_at) VALUES(?,?,?,?)',
                        (basis['scope_id'], basis['fingerprint'], run_id, _now(now)))
        elif current != basis['fingerprint']:
            raise BusinessError('STALE_EVIDENCE', '该批次不是当前采信版本，请按最新批次重新判定')
        active = _active_certificate(con, basis['scope_id'])
        if active:
            previous = json.loads(active['payload'])
            unchanged = (active['fingerprint'] == basis['fingerprint']
                         and previous.get('mode') == mode
                         and bool(previous.get('closed')) == draft['closed'])
            if unchanged:
                # 同批重跑：已确认的证书内容原样返回，不覆盖、不递增版本。
                con.execute('COMMIT')
                return {'certificate': json.loads(active['payload']), 'reused': True}
        version = _next_version(con, basis['scope_id'])
        issued = build_issued(draft, basis, run_id, version, mode, note, _now(now))
        if active:
            previous = json.loads(active['payload'])
            reason = ('同一证据作用域签发新版本'
                      if active['fingerprint'] == basis['fingerprint']
                      else invalidation_reason(previous['basis'], basis))
            con.execute("UPDATE certificates SET state='已失效', invalidated_at=?, reason=? WHERE seq=?",
                        (issued['issued_at'], reason, active['seq']))
        _write_version(con, issued, basis['fingerprint'], issued['issued_at'])
        con.execute('COMMIT')
    except Exception:
        _rollback(con)
        raise
    finally:
        con.close()
    return {'certificate': issued, 'reused': False}


def _cert_view(row):
    cert = json.loads(row['payload'])
    cert.update(state=row['state'], invalidated_at=row['invalidated_at'], reason=row['reason'],
                seq=row['seq'])
    return cert


def certificate_overview(path, state):
    basis = build_basis(state)
    con = connect(path)
    try:
        current = _fingerprint_row(con, basis['scope_id'])
        active = _active_certificate(con, basis['scope_id'])
        rows = con.execute('SELECT * FROM certificates WHERE scope_id=? ORDER BY version DESC',
                           (basis['scope_id'],)).fetchall()
    finally:
        con.close()
    certificate = _cert_view(active) if active else None
    stale_reason = None
    effective = False
    if certificate:
        if current is not None and current != certificate['evidence']['fingerprint']:
            stale_reason = invalidation_reason(certificate['basis'], basis)
        elif certificate['evidence']['fingerprint'] != basis['fingerprint']:
            stale_reason = '当前查看批次与证书证据指纹不一致'
        else:
            effective = True
    return {
        'scope_id': basis['scope_id'],
        'fingerprint': basis['fingerprint'],
        'current_fingerprint': current,
        'active': certificate,
        'effective': effective,
        'stale_reason': stale_reason,
        'history': [_cert_view(r) for r in rows],
    }


# ------------------------------------------------------------------ 正式归档

def _archive_id(run_id, certificate_id, basis):
    raw = json.dumps({'run_id': run_id, 'certificate_id': certificate_id,
                      'fingerprint': basis['fingerprint']}, sort_keys=True)
    return 'FORMAL-' + hashlib.sha256(raw.encode()).hexdigest()[:12].upper()


def _write_archive(con, payload):
    con.execute('INSERT INTO formal_archives(run_id,archive_id,scope_id,certificate_id,payload,created_at) '
                'VALUES(?,?,?,?,?,?)',
                (payload['run_id'], payload['archive_id'], payload['scope_id'],
                 payload['certificate_id'], json.dumps(payload, ensure_ascii=False), payload['created_at']))


def seal_archive(path, state, run_id, now=None):
    basis = build_basis(state)
    draft = build_certificate(state, basis)
    if not draft['closed']:
        raise BusinessError('EVIDENCE_OPEN', '证据未闭合，不得进入正式归档：' + '；'.join(draft['blockers']))
    archive = state.get('archive') or {}
    if archive.get('status') != '可归档':
        raise BusinessError('EVIDENCE_OPEN', '归档前置校验未通过')
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT * FROM formal_archives WHERE run_id=?', (run_id,)).fetchone()
        if existing:
            # 同批重跑不得覆盖已确认归档内容。
            con.execute('COMMIT')
            return {'archive': json.loads(existing['payload']), 'reused': True}
        current = _fingerprint_row(con, basis['scope_id'])
        if current is not None and current != basis['fingerprint']:
            raise BusinessError('STALE_EVIDENCE', '证据已变化，请按最新批次重新签发')
        active = _active_certificate(con, basis['scope_id'])
        if not active:
            raise BusinessError('CERTIFICATE_REQUIRED', '正式归档需要先签发基线适用性证书')
        certificate = _cert_view(active)
        if certificate['evidence']['fingerprint'] != basis['fingerprint']:
            raise BusinessError('STALE_CERTIFICATE', '证书与当前证据不一致，请重新签发')
        if certificate['mode'] != 'formal':
            raise BusinessError('EVIDENCE_OPEN', '限定签发只覆盖可用区间，不能作为正式归档依据')
        if not certificate['closed']:
            raise BusinessError('EVIDENCE_OPEN', '证书为限定签发，未覆盖全部受阻区间，不得正式归档')
        now_text = _now(now)
        payload = {
            'archive_id': _archive_id(run_id, certificate['certificate_id'], basis),
            'run_id': run_id,
            'scope_id': basis['scope_id'],
            'certificate_id': certificate['certificate_id'],
            'certificate_version': certificate['version'],
            'certificate_mode': certificate['mode'],
            'source_sha256': basis['source_sha256'],
            'fingerprint': basis['fingerprint'],
            'evidence': draft['evidence'],
            'coverage': draft['totals'],
            'seal': archive.get('seal'),
            'segments': archive.get('segments'),
            'devices': [{'device': d['device'], 'usable_seconds': d['usable_seconds'],
                         'blocked_seconds': d['blocked_seconds'],
                         'usable': d['usable'], 'blocked': d['blocked']} for d in draft['devices']],
            'certificate': certificate,
            'created_at': now_text,
        }
        _write_archive(con, payload)
        con.execute('COMMIT')
    except Exception:
        _rollback(con)
        raise
    finally:
        con.close()
    return {'archive': payload, 'reused': False}


def get_archive(path, run_id):
    con = connect(path)
    try:
        row = con.execute('SELECT * FROM formal_archives WHERE run_id=?', (run_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise BusinessError('NOT_FOUND', '该批次尚未正式归档', 404)
    return json.loads(row['payload'])


def archive_overview(path, run_id):
    """正式归档内容不可改写；有效性按所引用证书的当前状态派生，供审计追证。"""
    archive = get_archive(path, run_id)
    con = connect(path)
    try:
        row = con.execute('SELECT * FROM certificates WHERE certificate_id=?',
                          (archive['certificate_id'],)).fetchone()
        current = _fingerprint_row(con, archive['scope_id'])
    finally:
        con.close()
    if not row:
        status, reason = '来源证书缺失', '归档引用的证书不在库中'
    elif row['state'] != '已签发':
        status, reason = '已失效', row['reason'] or '证书已失效'
    elif current is not None and current != archive['fingerprint']:
        status, reason = '已失效', '上游证据已变化，归档所依据的证书不再适用'
    else:
        status, reason = '有效', ''
    return {**archive, 'derived_status': status, 'derived_reason': reason}
