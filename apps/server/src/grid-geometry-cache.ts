import {createHash} from 'node:crypto';
import type {Request} from 'express';
export type GeometryRef={path:(string|number)[];key:string};
/** Opt-in wire format only; every manifest and cache miss still passes the route's live authorization. */
export function gridGeometryResponse(req:Pick<Request,'headers'>,payload:any,scope:'current'|'snapshot'){
 const mode=req.headers['x-grid-geometry-cache'];if(typeof mode!=='string'||(mode!=='manifest'&&!mode.startsWith('load:')))return payload;
 const refs:GeometryRef[]=[],geometries:Record<string,unknown>={};
 function collect(parent:any,field:string,path:(string|number)[],id:unknown){const geometry=parent?.[field];if(!geometry||typeof geometry!=='object'||!['Polygon','MultiPolygon','GeometryCollection'].includes(geometry.type))return;const hash=createHash('sha256').update(JSON.stringify(geometry)).digest('hex'),key=`${scope}:${id}:${hash}`;refs.push({path:[...path,field],key});geometries[key]=geometry;if(mode==='manifest')parent[field]=null;}
 if(Array.isArray(payload.features))payload.features.forEach((feature:any,index:number)=>collect(feature,'geometry',['features',index],feature.id));
 collect(payload,'boundary',[],payload.gridId);
 if(payload.grid?.snapshot)collect(payload.grid.snapshot,'geometry',['grid','snapshot'],payload.grid.id);
 if(mode.startsWith('load:')){const requested=new Set(mode.slice(5).split(',').slice(0,64));return {geometryCacheVersion:1,geometries:Object.fromEntries(Object.entries(geometries).filter(([key])=>requested.has(key)))};}
 return {...payload,geometryCacheVersion:1,geometryRefs:refs};
}
