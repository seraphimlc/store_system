/** GeoJSON polygon coverage: outer and hole boundaries are covered. */
export function gridCovers(geometry:unknown,lon:number,lat:number):boolean{
 const shape=geometry as {type?:string;coordinates?:unknown};
 function ring(value:unknown):number{
  if(!Array.isArray(value)||value.length<4||value.some(point=>!Array.isArray(point)||!Number.isFinite(point[0])||!Number.isFinite(point[1])))return -2;
  if(value[0][0]!==value.at(-1)[0]||value[0][1]!==value.at(-1)[1])return -2;
  let inside=false,edge=false;
  for(let i=1;i<value.length;i++){const [x,y]=value[i-1],[a,b]=value[i];const cross=(lon-x)*(b-y)-(lat-y)*(a-x);
   if(Math.abs(cross)<=1e-12*Math.hypot(a-x,b-y)&&lon>=Math.min(x,a)&&lon<=Math.max(x,a)&&lat>=Math.min(y,b)&&lat<=Math.max(y,b))edge=true;
   if((y>lat)!==(b>lat)&&lon<(a-x)*(lat-y)/(b-y)+x)inside=!inside;
  }return edge?0:inside?1:-1;
 }
 const polygons=shape?.type==='Polygon'?[shape.coordinates]:shape?.type==='MultiPolygon'?shape.coordinates:null;
 if(!Array.isArray(polygons))return false;
 return polygons.some(p=>{if(!Array.isArray(p)||!p.length)return false;const rings=p.map(ring);return rings[0]>=0&&rings.every(result=>result!==-2)&&!rings.slice(1).some(result=>result===1);});
}
