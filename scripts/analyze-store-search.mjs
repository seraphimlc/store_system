import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const require = createRequire(new URL('../apps/server/package.json',import.meta.url));
const { Pool } = require('pg');
const args = new Map(process.argv.slice(2).flatMap((value, i, all) => value.startsWith('--') ? [[value.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : '']] : []));
const date = args.get('date') || new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo' }).format(new Date());
if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('--date must be YYYY-MM-DD');
const url = process.env.NEXT_DATABASE_URL || process.env.DATABASE_URL;
if (!url) throw new Error('NEXT_DATABASE_URL or DATABASE_URL is required');
const strategy = args.get('strategy') || '';
if(strategy.length>40)throw Error('--strategy is too long');
const pool = new Pool({ connectionString: url, max: 1, application_name: 'store-search-nightly-analysis' });
try {
  await pool.query('SET default_transaction_read_only=on');
  const { rows } = await pool.query(`WITH searches AS (
    SELECT * FROM bd_store_search_log WHERE page=1 AND query_text<>'' AND ($2='' OR strategy_version=$2)
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
  SELECT (SELECT jsonb_build_object(
     'requests_with_results',count(*) FILTER(WHERE w.matched_count>0),
     'opened_requests',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM bd_store_search_feedback f WHERE f.search_log_id=w.id AND f.kind='opened')),
     'confirmed_requests',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM bd_store_search_feedback f WHERE f.search_log_id=w.id AND f.kind='confirmed')),
     'suggestion_selected_requests',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM bd_store_search_feedback f WHERE f.search_log_id=w.id AND f.kind='suggestion_selected')),
     'completion_selected_requests',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM bd_store_search_feedback f WHERE f.search_log_id=w.id AND f.kind='completion_selected')),
     'confirmed_request_rate_percent',round(100.0*count(*) FILTER(WHERE EXISTS(SELECT 1 FROM bd_store_search_feedback f WHERE f.search_log_id=w.id AND f.kind='confirmed'))/nullif(count(*),0),2),
     'version', 'explicit-feedback-v1') FROM windowed w) AS feedback,
    (SELECT count(DISTINCT query_session_id)::int FROM windowed WHERE query_session_id IS NOT NULL) AS grid_page_sessions,
    (SELECT count(*)::int FROM windowed WHERE query_session_id IS NULL) AS requests_without_session,
    (SELECT round(avg(context_duration_ms),1) FROM windowed) AS avg_context_ms,
    (SELECT round(avg(query_duration_ms),1) FROM windowed) AS avg_query_ms,
    (SELECT coalesce(jsonb_agg(s),'[]'::jsonb) FROM (SELECT strategy_version,count(*)::int AS searches,count(*) FILTER(WHERE matched_count>0)::int AS result_searches,count(*) FILTER(WHERE EXISTS(SELECT 1 FROM bd_store_search_feedback f WHERE f.search_log_id=windowed.id AND f.kind='confirmed'))::int AS confirmed_requests,round(100.0*count(*) FILTER(WHERE matched_count>0)/nullif(count(*),0),2) AS result_rate_percent FROM windowed GROUP BY strategy_version ORDER BY strategy_version) s) AS by_strategy,
    (SELECT count(*)::int FROM windowed) AS searches,
    (SELECT count(*)::int FROM windowed WHERE query_text<>'') AS keyword_searches,
    (SELECT count(*)::int FROM windowed WHERE matched_count>0) AS result_searches,
    (SELECT round(100.0 * count(*) FILTER (WHERE matched_count>0) / nullif(count(*),0),2) FROM windowed) AS result_rate_percent,
    (SELECT count(*)::int FROM windowed WHERE query_text<>'' AND matched_count=0) AS zero_result_searches,
    (SELECT count(DISTINCT grid_task_id)::int FROM windowed WHERE query_text<>'' AND matched_count=0) AS zero_result_grids,
    (SELECT coalesce(round(avg(duration_ms)::numeric,1),0) FROM windowed) AS avg_duration_ms,
    (SELECT coalesce(jsonb_agg(z ORDER BY z.searches DESC),'[]'::jsonb) FROM zero z) AS zero_result_by_reason,
    (SELECT coalesce(jsonb_agg(q ORDER BY q.searches DESC),'[]'::jsonb) FROM (SELECT query_text AS query,count(*)::int AS searches,count(*) FILTER(WHERE matched_count=0)::int AS zero_results FROM windowed WHERE query_text<>'' GROUP BY query_text ORDER BY count(*) DESC LIMIT 50) q) AS top_queries` , [date,strategy]);
  const summary = { date, strategy:strategy||'all (compare by_strategy, not pooled rates)', generatedAt: new Date().toISOString(), ...rows[0], interpretation: { feedback: '打开不等于找到；confirmed为用户明确点击确认的请求级比例，未反馈不等于失败；仅首屏，分页事件留存但不混入首屏分母。grid_page_sessions为页面会话数，非独立找店意图。', result_rate: '有结果率=返回至少一条结果的首屏关键词搜索次数/首屏关键词搜索次数；无样本为null；不代表找到正确商户', filters_excluded: '原关键词有结果，状态筛选后为零', normalization_candidate: '全半角、大小写和空格归一后存在候选，需要核对', empty_grid: '当前任务网格无商户', unresolved: '仍待核查源数据、辖区绑定、快照和名称别名，不能仅凭零结果判定数据缺失', missing_counts: '按搜索累计的缺失字段观察数，不是去重商户数；源记录缺失也可能是新增门店', scope: '网格巡店搜索首屏请求，不含分页、定位选点；同词重试仍分别计数' } };
  const out = args.get('out');
  if (out) await writeFile(out, JSON.stringify(summary, null, 2) + '\n');
  console.log(JSON.stringify(summary, null, 2));
} finally { await pool.end(); }
