"""网格地区目录与版本查询；旧快照缺地区声明时仅归东京23区。"""
from sqlalchemy import func
from app.models import BdZoneRelease

REGIONS = {'tokyo23':'东京23区','tokyo26':'东京26市','kanagawa':'神奈川','saitama':'埼玉','chiba':'千叶'}


def release_ids(db, region):
    region_code = func.coalesce(BdZoneRelease.source['region_code'].as_string(),'tokyo23')
    if region == 'all':
        return db.query(BdZoneRelease.id).filter(region_code.in_(REGIONS))
    if region not in REGIONS: raise ValueError('地区不存在')
    return db.query(BdZoneRelease.id).filter(region_code == region)
