import {z} from 'zod';
const id=z.number().int().positive().max(2147483647);
export const selection=z.array(z.object({id,token:z.string().regex(/^[a-f0-9]{64}$/)}).strict()).min(1).max(500).refine(rows=>new Set(rows.map(row=>row.id)).size===rows.length);
export const movementRequest=z.discriminatedUnion('action',[
 z.object({action:z.literal('transfer'),teamId:id,selection,confirm:z.literal('TRANSFER_TASKS')}).strict(),
 z.object({action:z.literal('return-pool'),selection,confirm:z.literal('RETURN_TASKS')}).strict()
]);
export const movementQuery=z.object({q:z.string().trim().max(200).default(''),progress:z.enum(['0','1']).default('0'),page:z.coerce.number().int().min(1).max(10000).default(1)}).strict();
export const movementStamp="jsonb_build_object('task',to_jsonb(t),'assignees',(SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.person_code),'[]') FROM bd_task_assign a WHERE a.task_id=t.id),'progress',(SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.progress_date,p.id),'[]') FROM bd_task_progress p WHERE p.task_id=t.id))::text";

export const uniformProgressRequest=z.object({selection,pct:z.number().int().min(0).max(100),note:z.string().trim().max(4000).default(''),confirm:z.literal('SET_TASK_PROGRESS')}).strict();
