import {arena,type JsonGeometry} from '../../../packages/domain/src/grid-geometry';
import {BusinessError} from './errors';
export interface AutoGrid{id:number;geometry:JsonGeometry;workload:number}
export interface AutoMember{id:number;name:string;load:number;efficiency:number}
/** Grow each owner's connected set; never fabricate a plan for disconnected input. */
export async function connectedPlan(grids:AutoGrid[],members:AutoMember[],weights:Record<string,number>){
 if(!grids.length||!members.length)throw new BusinessError('AUTO_ASSIGN_EMPTY','没有可分配网格或参与人员',409);
 return arena(s=>{
  const shapes=grids.map(g=>s.read(g.geometry)),centers=shapes.map(g=>{const p=s.json(s.own(s.g.GEOSPointOnSurface(g))) as {coordinates:number[]};return p.coordinates;});
  const boundaries=shapes.map(g=>s.own(s.g.GEOSBoundary(g))),graph=grids.map(()=>new Set<number>());
  for(let i=0;i<grids.length;i++)for(let j=i+1;j<grids.length;j++)if(s.length(s.own(s.g.GEOSIntersection(boundaries[i],boundaries[j])))>1e-9){graph[i].add(j);graph[j].add(i);}
  const seen=new Set([0]),queue=[0];while(queue.length)for(const j of graph[queue.pop()!])if(!seen.has(j)){seen.add(j);queue.push(j);}
  if(seen.size!==grids.length)throw new BusinessError('AUTO_ASSIGN_DISCONNECTED','网格范围不连通，无法保证连续分配；请手动分配',409);
  const active=[...members].sort((a,b)=>a.load-b.load||a.id-b.id).slice(0,grids.length),owners=new Map<number,number>(),loads=new Map(active.map(m=>[m.id,m.load]));
  const dist=(i:number,j:number)=>Math.hypot(centers[i][0]-centers[j][0],centers[i][1]-centers[j][1]);const seeds=[0];
  while(seeds.length<active.length){let best=-1,score=-1;for(let i=0;i<grids.length;i++)if(!seeds.includes(i)){const d=Math.min(...seeds.map(j=>dist(i,j)));if(d>score){best=i;score=d;}}seeds.push(best);}
  seeds.forEach((i,k)=>{owners.set(i,active[k].id);loads.set(active[k].id,loads.get(active[k].id)!+Math.max(1,grids[i].workload));});
  while(owners.size<grids.length){let target=-1,owner=-1,best=Infinity;for(const [from,id]of owners){const member=active.find(m=>m.id===id)!;for(const j of graph[from])if(!owners.has(j)){const score=(loads.get(id)!+Math.max(1,grids[j].workload))/(member.efficiency*weights[String(id)])+dist(from,j);if(score<best){best=score;target=j;owner=id;}}}if(target<0)throw new BusinessError('AUTO_ASSIGN_DISCONNECTED','不能生成完整连续方案',409);owners.set(target,owner);loads.set(owner,loads.get(owner)!+Math.max(1,grids[target].workload));}
  return grids.map((g,i)=>({gridId:g.id,assigneeId:owners.get(i)!,workload:g.workload}));
 });
}
