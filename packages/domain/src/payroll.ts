/** Deterministic payroll rules. No persistence, model calls or floating-point currency arithmetic. */
export interface BonusConfig {group:number;amount:number;perPoint:number}
export interface BonusEnvironment {group?:number;amount?:number;perPoint?:number;groupSchedule?:string;amountSchedule?:string}
function integer(value:number,name:string,minimum=0){if(!Number.isSafeInteger(value)||value<minimum)throw new RangeError(name+' must be a safe integer >= '+minimum);return value;}
function number(value:bigint){const result=Number(value);if(!Number.isSafeInteger(result))throw new RangeError('Money exceeds safe integer range');return result;}
export function validMonth(month:string){return /^\d{4}-(0[1-9]|1[0-2])$/.test(month);}
export function previousMonth(month:string){if(!validMonth(month))throw new RangeError('Invalid month');const [year,part]=month.split('-').map(Number);return part===1?`${year-1}-12`:`${year}-${String(part-1).padStart(2,'0')}`;}
function schedule(raw:string){return raw.split(',').flatMap(part=>{const [month,value]=part.trim().split('=').map(value=>value.trim());return month?.length===7&&/^\d+$/.test(value??'')?[[month,Number(value)] as const]:[];}).sort(([a],[b])=>a.localeCompare(b));}
export function bonusConfig(month:string,environment:BonusEnvironment={},database?:Partial<BonusConfig>):BonusConfig{
 if(!validMonth(month))throw new RangeError('Invalid month');
 let group=environment.group??68,amount=environment.amount??3000;
 if(database){group=database.group||68;amount=database.amount||3000;}
 else{
  for(const [start,value]of schedule(environment.groupSchedule??'2026-09=75'))if(month>=start)group=value;
  for(const [start,value]of schedule(environment.amountSchedule??'2026-09=1250'))if(month>=start)amount=value;
 }
 const perPoint=database?.perPoint||environment.perPoint||250;
 return {group:integer(group,'Bonus group',1),amount:integer(amount,'Bonus amount'),perPoint:integer(perPoint,'Point price')};
}
export function salaryFor(points:number,config:BonusConfig){
 integer(points,'Points',Number.MIN_SAFE_INTEGER);integer(config.group,'Bonus group',1);integer(config.amount,'Bonus amount');integer(config.perPoint,'Point price');
 if(points<=0)return 0;const p=BigInt(points);return number(p*BigInt(config.perPoint)+(p/BigInt(config.group))*BigInt(config.amount));
}
export function halfGross(h1:number,h2:number,config:BonusConfig){
 integer(h1,'H1 points');integer(h2,'H2 points');integer(config.group,'Bonus group',1);integer(config.amount,'Bonus amount');integer(config.perPoint,'Point price');
 const a=BigInt(h1),b=BigInt(h2),group=BigInt(config.group),bonus=BigInt(config.amount),price=BigInt(config.perPoint);
 const firstBonus=a/group*bonus,secondBonus=(a%group+b)/group*bonus;
 return {h1:{points:h1,bonus:number(firstBonus),amount:number(a*price+firstBonus)},h2:{points:h2,bonus:number(secondBonus),amount:number(b*price+secondBonus)}};
}
export function payPlan(firstGross:number,secondGross:number,carry:number,registeredFirstAdjustment?:number){
 for(const [name,value]of Object.entries({firstGross,secondGross,carry}))integer(value,name,name==='carry'?Number.MIN_SAFE_INTEGER:0);
 if(registeredFirstAdjustment!==undefined)integer(registeredFirstAdjustment,'Registered adjustment',Number.MIN_SAFE_INTEGER);
 const first=BigInt(firstGross),second=BigInt(secondGross),debt=BigInt(carry);
 const absorbed=registeredFirstAdjustment===undefined?(debt>0n?debt:debt> -first?debt:-first):BigInt(registeredFirstAdjustment);
 const remaining=debt-absorbed;
 return {h1:[carry,number(first+debt<0n?0n:first+debt)] as [number,number],h2:[number(remaining),number(second+remaining<0n?0n:second+remaining)] as [number,number]};
}
export interface AdjustmentBalance {id:number;sourceMonth:string;remaining:number;settled:number}
export function allocateFifo(adjustments:AdjustmentBalance[],absorbed:number){
 integer(absorbed,'Absorbed amount',Number.MIN_SAFE_INTEGER);let rest=BigInt(absorbed);const allocations:{adjustId:number;sourceMonth:string;amount:number;remaining:number;settled:number}[]=[];
 for(const entry of [...adjustments].sort((a,b)=>a.sourceMonth.localeCompare(b.sourceMonth)||a.id-b.id)){
  integer(entry.remaining,'Remaining',Number.MIN_SAFE_INTEGER);integer(entry.settled,'Settled',Number.MIN_SAFE_INTEGER);
  const remaining=BigInt(entry.remaining);if(rest===0n)break;if(remaining===0n||(remaining>0n)!==(rest>0n))continue;
  const absolute=(value:bigint)=>value<0n?-value:value;const take=absolute(rest)<=absolute(remaining)?rest:remaining;
  allocations.push({adjustId:entry.id,sourceMonth:entry.sourceMonth,amount:number(take),remaining:number(remaining-take),settled:number(BigInt(entry.settled)+take)});rest-=take;
 }
 return {allocations,unallocated:number(rest)};
}
