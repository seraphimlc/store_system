import {z} from 'zod';import {validDate} from './business-dates';
const date=z.string().refine(value=>validDate(value)&&!value.startsWith('0000'));
export const reviewQuery=z.object({kind:z.enum(['pending','settled']).default('pending'),page:z.coerce.number().int().min(1).max(10000).default(1),dateFrom:date.optional(),dateTo:date.optional()}).strict().refine(value=>!value.dateFrom||!value.dateTo||value.dateFrom<=value.dateTo);
export const reviewSelection=z.array(z.object({id:z.number().int().positive().max(2147483647),token:z.string().regex(/^[a-f0-9]{64}$/)}).strict()).min(1).max(500).refine(rows=>new Set(rows.map(row=>row.id)).size===rows.length);
export const confirmReviews=z.object({selection:reviewSelection.optional(),confirm:z.literal('CONFIRM_REVIEWS')}).strict();
export const reopenReviews=z.object({selection:reviewSelection,forcePct:z.number().int().min(0).max(100).optional(),noOriginPct:z.number().int().min(0).max(100).optional(),note:z.string().trim().max(4000).default(''),confirm:z.literal('REOPEN_REVIEWS')}).strict();
export const reviewStamp="jsonb_build_object('progress',to_jsonb(p),'task',jsonb_build_object('id',t.id,'team',t.team_id,'pct',t.pct,'state',t.state,'updated',t.updated_at::text))::text";
