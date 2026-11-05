"""Merchant photos and ID-only Places enrichment. Never rewrite source merchant IDs."""
from __future__ import annotations

import hashlib
import io
import math
import os
import warnings
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models import BdMerchantDetail, BdStoreTask
from app.services import bd_zone_operations as ops

MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 24_000_000
OUTCOMES = ('completed', 'undecided', 'incomplete')


def detail_map(db, store_ids):
    ids = list(set(store_ids))
    return {row.store_id: row for row in db.query(BdMerchantDetail).filter(
        BdMerchantDetail.store_id.in_(ids)).all()} if ids else {}


def locked_detail(db, store_id):
    # Callers hold the current batch lock; PostgreSQL also serializes cross-batch edits.
    if db.bind.dialect.name == 'postgresql':
        from sqlalchemy import text
        lock_id = int.from_bytes(hashlib.sha256(store_id.encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:id)'), {'id': lock_id})
    row = db.query(BdMerchantDetail).filter_by(store_id=store_id).populate_existing().with_for_update().first()
    if row is None:
        row = BdMerchantDetail(store_id=store_id, photo_urls=[])
        db.add(row)
        db.flush()
    return row


def sanitize_photo(data):
    """Decode and re-encode to JPEG: validate bytes, bound memory and remove EXIF/GPS."""
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise ops.OperationError('每张照片须不超过5MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > MAX_PIXELS:
                    raise ValueError()
                image.load()
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((1920, 1920))
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=85, optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ops.OperationError('照片须为有效的JPEG、PNG或WebP图片，且不超过2400万像素') from None


def upload_photo(store_id, data):
    """Only this application-owned prefix is writable; object key is retry-stable."""
    endpoint = os.environ.get('STORE_FILES_OSS_ENDPOINT', '').strip()
    bucket_name = os.environ.get('STORE_FILES_OSS_BUCKET', '').strip()
    cdn = os.environ.get('STORE_FILES_CDN_BASE', '').rstrip('/')
    if (not endpoint.startswith('https://') or not bucket_name or not cdn.startswith('https://')
            or not os.environ.get('OSS_ACCESS_KEY_ID') or not os.environ.get('OSS_ACCESS_KEY_SECRET')):
        raise ops.OperationError('照片存储未配置，请管理员配置OSS上传凭据')
    key = 'store-system-files/' + hashlib.sha256(store_id.encode()).hexdigest()[:24] + '/' + hashlib.sha256(data).hexdigest() + '.jpg'
    try:
        import oss2
        from oss2.credentials import EnvironmentVariableCredentialsProvider
        bucket = oss2.Bucket(oss2.ProviderAuth(EnvironmentVariableCredentialsProvider()), endpoint,
                             bucket_name, connect_timeout=20)
        result = bucket.put_object(key, data, headers={'Content-Type': 'image/jpeg',
                                                      'Cache-Control': 'public,max-age=31536000,immutable'})
        if result.status != 200:
            raise RuntimeError()
    except Exception:
        raise ops.OperationError('照片上传失败，请重试；本次巡店未保存') from None
    return cdn + '/' + quote(key, safe='/')


def lookup_place_id(name, lat, lon):
    """ID-only response: a unique result is a candidate, not a verified identity."""
    key = os.environ.get('GOOGLE_PLACES_API_KEY', '')
    if not key:
        return {'google_place_id': None, 'place_lookup_state': 'unconfigured'}
    try:
        with httpx.Client(timeout=12, trust_env=False) as client:
            response = client.post('https://places.googleapis.com/v1/places:searchText',
                headers={'X-Goog-Api-Key': key, 'X-Goog-FieldMask': 'places.id'},
                json={'textQuery': name, 'languageCode': 'ja',
                      'locationBias': {'circle': {'center': {'latitude': lat, 'longitude': lon}, 'radius': 50.0}}})
            response.raise_for_status()
            places = response.json().get('places', [])
        if not isinstance(places, list):
            raise ValueError()
        ids = list(dict.fromkeys(p['id'] for p in places if isinstance(p, dict)
                                and isinstance(p.get('id'), str) and 0 < len(p['id']) <= 255))
        if len(ids) == 1:
            return {'google_place_id': ids[0], 'place_lookup_state': 'candidate'}
        return {'google_place_id': None, 'place_lookup_state': 'ambiguous' if ids else 'no_match'}
    except Exception:
        return {'google_place_id': None, 'place_lookup_state': 'failed'}


def save_visit(db, user, identity, revision, request_id, status, note, outcome, keep_photos, photos):
    from app.services import bd_hierarchical_tasks as tasks
    initial = db.get(BdStoreTask, identity)
    if initial is None:
        raise ops.OperationError('店铺任务不存在')
    main, _, grid = tasks.context(db, user, initial.grid_task_id, lock=True, execute=True)
    store = ops.locked(db, BdStoreTask, identity)
    if outcome not in OUTCOMES or status not in tasks.STORE_STATUSES or len(note) > 2000:
        raise ops.OperationError('门店状态或作业结果无效')
    import uuid
    try:
        uuid.UUID(request_id)
    except (ValueError, TypeError):
        raise ops.OperationError('提交标识无效')
    # Validate all bytes and selection before any external writes.
    if len(photos) > MAX_PHOTOS or len(keep_photos) + len(photos) > MAX_PHOTOS:
        raise ops.OperationError('每家店铺最多保存3张照片')
    clean = [sanitize_photo(data) for data in photos]
    profile = locked_detail(db, store.store_id)
    if len(set(keep_photos)) != len(keep_photos) or set(keep_photos) - set(profile.photo_urls or []):
        raise ops.OperationError('照片选择已变化，请刷新后重试')
    if store.request_id != request_id:
        if main.status == 'completed':
            raise ops.OperationError('该批次已完成')
        ops.revision_ok(store, revision)
        if db.query(BdStoreTask.id).filter_by(request_id=request_id).first():
            raise ops.OperationError('提交标识冲突')
    urls = list(keep_photos)
    urls.extend(upload_photo(store.store_id, data) for data in clean)
    urls = list(dict.fromkeys(urls))
    grid_id = tasks.complete_store(db, user, identity, revision, request_id, status, note,
                                   outcome=outcome, photo_urls=urls)
    profile.photo_urls = urls
    profile.revision += 1
    from datetime import datetime
    profile.updated_at = datetime.utcnow()
    return grid_id
