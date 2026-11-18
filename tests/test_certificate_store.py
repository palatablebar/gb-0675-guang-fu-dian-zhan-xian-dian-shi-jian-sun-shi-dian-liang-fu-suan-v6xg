import pytest
from copy import deepcopy
from app import store
from app.errors import BusinessError
from app.engine import evaluate


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / 'cert.sqlite')


def _bounded_dirty(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    return store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')


def test_issue_bounded_on_open_evidence(db):
    result = _bounded_dirty(db)
    cert = result['certificate']
    assert result['reused'] is False
    assert cert['mode'] == 'bounded' and cert['closed'] is False
    inv_a = next(d for d in cert['devices'] if d['device'] == 'INV-A')
    assert inv_a['usable_seconds'] == 600 and inv_a['blocked_seconds'] == 1200
    assert cert['blockers']


def test_formal_signing_refuses_open_evidence(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    with pytest.raises(BusinessError) as error:
        store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='formal')
    assert error.value.code == 'EVIDENCE_OPEN'
    assert store.certificate_overview(db, dirty)['active'] is None


def test_clean_certificate_is_closed(db):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    cert = store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')['certificate']
    assert cert['closed'] is True
    assert cert['coverage']['coverage'] == 1.0
    assert cert['certificate_id'].endswith('-v1')


def test_rerun_same_evidence_reuses_confirmed_content(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    first = store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')['certificate']
    again = store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')
    assert again['reused'] is True
    assert again['certificate'] == first
    overview = store.certificate_overview(db, dirty)
    assert len(overview['history']) == 1


def test_upstream_admission_change_invalidates_then_reissues(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    first = store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')['certificate']
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    overview = store.certificate_overview(db, clean)
    assert overview['active'] is None
    stale = next(c for c in overview['history'] if c['certificate_id'] == first['certificate_id'])
    assert stale['state'] == '已失效'
    assert '遥测采信变化' in stale['reason']
    assert stale['invalidated_at']
    second = store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')['certificate']
    assert second['version'] == 2
    assert second['closed'] is True


def test_old_batch_cannot_issue_after_evidence_advances(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    with pytest.raises(BusinessError) as error:
        store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')
    assert error.value.code == 'STALE_EVIDENCE'


def test_mapping_change_invalidates(db):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')
    mapped = deepcopy(clean)
    mapped['context']['devices']['INV-B']['capacity_kw'] = '80'
    store.save(db, mapped, advance_scope=True)
    overview = store.certificate_overview(db, mapped)
    assert overview['active'] is None
    assert '设备映射变化' in overview['history'][0]['reason']


def test_failed_issue_rolls_back_previous_version(db, monkeypatch):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    first = store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')['certificate']
    # 诱导写入阶段失败：旧版本必须保持有效，不得出现半途版本。
    monkeypatch.setattr(store, '_write_version', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full')))
    with pytest.raises(RuntimeError):
        store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='bounded', note='重判')
    monkeypatch.undo()
    overview = store.certificate_overview(db, clean)
    assert overview['active']['certificate_id'] == first['certificate_id']
    assert overview['active']['state'] == '已签发'
    assert [c['version'] for c in overview['history']] == [1]


def test_seal_requires_closed_certificate(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    with pytest.raises(BusinessError) as error:
        store.seal_archive(db, dirty, dirty['manifest']['run_id'])
    assert error.value.code == 'EVIDENCE_OPEN'
    store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')
    with pytest.raises(BusinessError) as error:
        store.seal_archive(db, dirty, dirty['manifest']['run_id'])
    assert error.value.code == 'EVIDENCE_OPEN'


def test_seal_clean_writes_once_and_never_overwrites(db):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    with pytest.raises(BusinessError) as error:
        store.seal_archive(db, clean, clean['manifest']['run_id'])
    assert error.value.code == 'CERTIFICATE_REQUIRED'
    store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')
    first = store.seal_archive(db, clean, clean['manifest']['run_id'])
    again = store.seal_archive(db, clean, clean['manifest']['run_id'])
    assert first['reused'] is False and again['reused'] is True
    assert again['archive'] == first['archive']
    assert store.get_archive(db, clean['manifest']['run_id'])['archive_id'] == first['archive']['archive_id']


def test_invalidated_certificate_blocks_seal(db):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')
    store.invalidate_scope(db, store.certificate_overview(db, clean)['scope_id'], '人工复核撤销')
    with pytest.raises(BusinessError) as error:
        store.seal_archive(db, clean, clean['manifest']['run_id'])
    assert error.value.code == 'CERTIFICATE_REQUIRED'


def test_certificate_page_shows_devices_and_blockers(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'ui.sqlite'))
    client = TestClient(main.app)
    dirty = client.post('/api/runs', json={}).json()
    page = client.get('/certificate?run_id=' + dirty['manifest']['run_id'])
    assert page.status_code == 200
    assert 'INV-A' in page.text and 'INV-B' in page.text
    assert '受阻' in page.text and '同区、同时刻、同辐照档的健康对照不足' in page.text
    assert '证据未闭合，不得正式归档' in page.text


def test_issue_api_and_archive_gate(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'ui.sqlite'))
    client = TestClient(main.app)
    dirty = client.post('/api/runs', json={}).json()
    rid = dirty['manifest']['run_id']
    assert client.post('/api/certificate/issue', json={'run_id': rid, 'mode': 'formal'}).status_code == 422
    issued = client.post('/api/certificate/issue', json={'run_id': rid, 'mode': 'bounded'})
    assert issued.status_code == 200 and issued.json()['certificate']['mode'] == 'bounded'
    sealed = client.post('/api/runs/' + rid + '/seal')
    assert sealed.status_code == 422 and sealed.json()['code'] == 'EVIDENCE_OPEN'
    clean = client.post('/api/runs', json={'dataset': 'clean'}).json()
    cid = clean['manifest']['run_id']
    assert client.post('/api/certificate/issue', json={'run_id': cid, 'mode': 'formal'}).status_code == 200
    assert client.post('/api/runs/' + cid + '/seal').status_code == 200
    bundle = client.get('/api/export/' + cid)
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()
    assert {'manifest.json', 'evidence.json', 'certificate.json', 'archive.json'} <= set(names)


def test_formal_export_carries_certificate(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    import io, json, zipfile
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'ui.sqlite'))
    client = TestClient(main.app)
    clean = client.post('/api/runs', json={'dataset': 'clean'}).json()
    rid = clean['manifest']['run_id']
    client.post('/api/certificate/issue', json={'run_id': rid, 'mode': 'formal'})
    client.post('/api/runs/' + rid + '/seal')
    archive = zipfile.ZipFile(io.BytesIO(client.get('/api/export/' + rid).content))
    cert = json.loads(archive.read('certificate.json'))
    manifest = json.loads(archive.read('manifest.json'))
    assert cert['closed'] is True
    assert manifest['certificate_id'] == cert['certificate_id']
    assert manifest['fingerprint'] == cert['evidence']['fingerprint']


def test_archive_status_derives_from_certificate(db):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')
    sealed = store.seal_archive(db, clean, clean['manifest']['run_id'])['archive']
    assert store.archive_overview(db, clean['manifest']['run_id'])['derived_status'] == '有效'
    store.invalidate_scope(db, sealed['scope_id'], '审计撤销')
    overview = store.archive_overview(db, clean['manifest']['run_id'])
    assert overview['derived_status'] == '已失效'
    assert overview['derived_reason'] == '审计撤销'
    # 归档内容本身不可改写。
    assert overview['archive_id'] == sealed['archive_id']
    assert overview['certificate_id'] == sealed['certificate_id']


def test_rerun_of_current_batch_can_advance_stale_scope(db):
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    # 当前采信批次回退到干净批：重跑它就是新的采信结论，旧证书失效后可按当前证据重判。
    rerun = store.save(db, clean, advance_scope='auto')
    assert rerun['manifest']['run_id'] == clean['manifest']['run_id']
    overview = store.certificate_overview(db, clean)
    assert overview['active'] is None
    assert overview['history'][0]['state'] == '已失效'


def test_rerun_of_superseded_batch_does_not_rewind_scope(db):
    dirty = evaluate()
    store.save(db, dirty, advance_scope=True)
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    store.issue_certificate(db, clean, clean['manifest']['run_id'], mode='formal')
    store.save(db, dirty, advance_scope='auto')
    overview = store.certificate_overview(db, clean)
    assert overview['active'] is not None and overview['effective'] is True


def test_first_issuance_sets_scope_baseline(db):
    dirty = evaluate()
    store.save(db, dirty)  # 未经导入入口落库（例如启动载入）
    store.issue_certificate(db, dirty, dirty['manifest']['run_id'], mode='bounded')
    assert store.scope_run(db, store.certificate_overview(db, dirty)['scope_id']) == dirty['manifest']['run_id']
    clean = evaluate({'dataset': 'clean'})
    store.save(db, clean, advance_scope=True)
    assert store.certificate_overview(db, clean)['active'] is None
