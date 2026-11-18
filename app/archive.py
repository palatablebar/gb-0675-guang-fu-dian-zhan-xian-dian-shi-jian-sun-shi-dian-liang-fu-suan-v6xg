import io, csv, json, zipfile
from app.errors import BusinessError


def _manifest(result):
    return {**result['manifest'], 'revision': result.get('revision', 1),
            'status': result.get('status', '待复核')}


def _evidence(result):
    return {'counts': result['counts'], 'views': result['views'],
            'quarantine': result['quarantine']}


def serialize(result):
    """普通（未正式归档）运行包：仅含当前批次证据。"""
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN', '批次尚未完成')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',
                         json.dumps(_manifest(result), ensure_ascii=False, indent=2))
        archive.writestr('evidence.json',
                         json.dumps(_evidence(result), ensure_ascii=False, indent=2))
    return buffer.getvalue()


def serialize_formal(result, formal):
    """正式归档包：批次证据 + 基线适用性证书 + 归档清单，三者同一指纹。"""
    if not formal.get('fingerprint'):
        raise BusinessError('INCOMPLETE_RUN', '正式归档缺少证据指纹')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {**_manifest(result), 'archive_id': formal['archive_id'],
                    'scope_id': formal['scope_id'], 'certificate_id': formal['certificate_id'],
                    'certificate_version': formal['certificate_version'],
                    'fingerprint': formal['fingerprint'], 'seal': formal.get('seal')}
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('evidence.json',
                         json.dumps(_evidence(result), ensure_ascii=False, indent=2))
        archive.writestr('certificate.json',
                         json.dumps(formal['certificate'], ensure_ascii=False, indent=2))
        archive.writestr('archive.json', json.dumps(formal, ensure_ascii=False, indent=2))
    return buffer.getvalue()
