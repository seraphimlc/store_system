/** Legacy station task state, independent from persistence and settlement. */
export function stationTaskState(pct:number,assignees:number){if(pct>=100)return 'done';return pct>0||assignees>0?'doing':'unassigned';}
export function staffProgressLocked(review:string|undefined){return review==='confirmed';}
export function adminMayAdjust(review:string|undefined){return review==='confirmed'||review==='adjusted'||review==='rejected';}
