import {readdir,readFile,stat} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {createHash} from 'node:crypto';
import {osmRiverFaces,type GridGeometry,type OsmWaterElement} from '../../../packages/domain/src/grid-geometry';
import {BusinessError} from './errors';
type Evidence={fingerprint:string;faces:GridGeometry[];files:string[]};
let cached:{key:string;promise:Promise<Evidence>}|undefined;
export async function riverEvidence():Promise<Evidence>{
 try{
  const root=resolve(process.env.NEXT_ZONE_DATA_DIRECTORY??'data/bd_zones');
  let names:string[];try{names=(await readdir(root)).filter(name=>name.endsWith('.roads-v1.source.json')).sort();}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')names=[];else throw error;}
  const signatures=await Promise.all(names.map(async name=>{const info=await stat(join(root,name));if(!info.isFile()||info.size>64*1024*1024)throw Error('Invalid evidence');return [name,info.size,info.mtimeMs,info.ctimeMs];}));
  const key=JSON.stringify([root,signatures]);if(cached?.key===key)return cached.promise;
  const promise=(async()=>{const hash=createHash('sha256'),elements:OsmWaterElement[]=[];for(const name of names){const bytes=await readFile(join(root,name));hash.update(name).update('\0').update(bytes);const document=JSON.parse(bytes.toString('utf8'));if(!Array.isArray(document.elements)||document.remark)throw Error('Incomplete evidence');elements.push(...document.elements);}return {fingerprint:hash.digest('hex'),faces:await osmRiverFaces(elements),files:names};})();
  cached={key,promise};return await promise;
 }catch{cached=undefined;throw new BusinessError('RIVER_EVIDENCE_INVALID','河道证据文件无效或不完整，不能预览合并',409);}
}
