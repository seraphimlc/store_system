import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const require = createRequire(new URL('../apps/server/package.json',import.meta.url));
const { Pool } = require('pg');
const args = new Map(process.argv.slice(2).flatMap((value, i, all) => value.startsWith('--') ? [[value.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : '']] : []));
const date = args.get('date') || new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('--date must be YYYY-MM-DD');
const url = process.env.NEXT_DATABASE_URL || process.env.DATABASE_URL;
if (!url) throw new Error('NEXT_DATABASE_URL or DATABASE_URL is required');
const pool = new Pool({ connectionString: url, max: 1, application_name: 'store-search-nightly-analysis' });
try {
  await pool.query('SET default_transaction_read_only=on');
  const { rows } = await pool.query(`WITH searches AS (
    SELECT * FROM bd_store_search_log WHERE page=1 AND query_text<>''
  ), windowed AS (SELECT * FROM searches
    WHERE created_at >= (($1::date::timestamp AT TIME ZONE 'Asia/Tokyo') AT TIME ZONE 'UTC')
      AND created_at < ((($1::date + 1)::timestamp AT TIME ZONE 'Asia/Tokyo') AT TIME ZONE 'UTC')
  ), zero AS (
    SELECT no_result_reason AS reason, count(*)::int AS searches, count(DISTINCT grid_task_id)::int AS grids,
           coalesce(sum(missing_name_count),0)::int AS missing_name,
           coalesce(sum(missing_address_count),0)::int AS missing_address,
           coalesce(sum(missing_source_count),0)::int AS missing_source,
           coalesce(sum(missing_location_count),0)::int AS missing_location
    FROM windowed WHERE matched_count=0 AND query_text<>'' GROUP BY no_result_reason
  )
  SELECT (SELECT count(*)::int FROM windowed) AS searches,
    (SELECT count(*)::int FROM windowed WHERE query_text<>'') AS keyword_searches,
    (SELECT count(*)::int FROM windowed WHERE query_text<>'' AND matched_count=0) AS zero_result_searches,
    (SELECT count(DISTINCT grid_task_id)::int FROM windowed WHERE query_text<>'' AND matched_count=0) AS zero_result_grids,
    (SELECT coalesce(round(avg(duration_ms)::numeric,1),0) FROM windowed) AS avg_duration_ms,
    (SELECT coalesce(jsonb_agg(z ORDER BY z.searches DESC),'[]'::jsonb) FROM zero z) AS zero_result_by_reason,
    (SELECT coalesce(jsonb_agg(q ORDER BY q.searches DESC),'[]'::jsonb) FROM (SELECT query_text AS query,count(*)::int AS searches,count(*) FILTER(WHERE matched_count=0)::int AS zero_results FROM windowed WHERE query_text<>'' GROUP BY query_text ORDER BY count(*) DESC LIMIT 50) q) AS top_queries` , [date]);
  const summary = { date, generatedAt: new Date().toISOString(), ...rows[0], interpretation: { filters_excluded: '原关键词有结果，状态筛选后为零', normalization_candidate: '全半角、大小写和空格归一后存在候选，需要核对', empty_grid: '当前任务网格无商户', unresolved: '仍待核查源数据、辖区绑定、快照和名称别名，不能仅凭零结果判定数据缺失', missing_counts: '按搜索累计的缺失字段观察数，不是去重商户数；源记录缺失也可能是新增门店', scope: '网格巡店搜索首屏请求，不含分页、定位选点；同词重试仍分别计数' } };
  const out = args.get('out');
  if (out) await writeFile(out, JSON.stringify(summary, null, 2) + '\n');
  console.log(JSON.stringify(summary, null, 2));
} finally { await pool.end(); }
