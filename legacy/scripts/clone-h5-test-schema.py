import os,sys,json,re
from urllib.parse import urlparse
from sqlalchemy import create_engine,MetaData,text
source=os.environ['NEXT_TEST_DATABASE_URL'].replace('postgresql:','postgresql+psycopg2:').replace('postgres:','postgresql+psycopg2:')
target=os.environ['H5_TARGET_URL'].replace('postgresql:','postgresql+psycopg2:').replace('postgres:','postgresql+psycopg2:')
a,b=urlparse(source),urlparse(target)
if a.hostname not in ('localhost','127.0.0.1') or a.path!='/store_settle_refactor_p1' or b.hostname!=a.hostname or not re.fullmatch(r'/store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1',b.path):
    raise RuntimeError('Only a disposable local schema target is allowed')
s=create_engine(source,connect_args={'options':'-c default_transaction_read_only=on'});t=create_engine(target)
m=MetaData()
with s.connect() as c:
    sequences=c.execute(text("SELECT schemaname,sequencename,start_value,increment_by,min_value,max_value,cache_size,cycle FROM pg_sequences WHERE schemaname='public'")).mappings().all()
    m.reflect(bind=c,schema='public')
with t.begin() as c:
    for r in sequences:
        name=r['sequencename'].replace('"','""')
        c.execute(text(f'CREATE SEQUENCE IF NOT EXISTS public."{name}" START {r["start_value"]} INCREMENT {r["increment_by"]} MINVALUE {r["min_value"]} MAXVALUE {r["max_value"]} CACHE {r["cache_size"]} '+('CYCLE' if r['cycle'] else 'NO CYCLE')))
m.create_all(t)
print('Copied table definitions only:',len(m.tables))
s.dispose();t.dispose()
