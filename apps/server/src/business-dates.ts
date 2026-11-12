export function jstToday(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit' }).formatToParts(now);
  const values = Object.fromEntries(parts.map(part => [part.type,part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
export function validDate(value:string) { return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value+'T00:00:00Z')) && new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value; }
export function monthRange(value:string) {
  if(!/^\d{4}-\d{2}$/.test(value)||!validDate(value+'-01'))throw new Error('Invalid month');
  const [year,month]=value.split('-').map(Number);
  return { start:value+'-01',end:new Date(Date.UTC(year,month,0)).toISOString().slice(0,10) };
}
