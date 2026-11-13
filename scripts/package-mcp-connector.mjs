import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {dirname,resolve,join} from 'node:path';
import {readFile,mkdir,rename,unlink} from 'node:fs/promises';
import {createWriteStream} from 'node:fs';
import {finished} from 'node:stream/promises';
export const connectorFiles=['connector-meta.json','mcp.json','oauth-info.json','icon.svg','skills/visit-settle/SKILL.md','README.md'];
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),source=join(root,'deploy-next/connector/visit-settle');
export function connectorBase(value){let url;try{url=new URL(value);}catch{throw Error('Provide --base-url with a valid service origin');}
 if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||!(url.protocol==='https:'||url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)))throw Error('Connector requires HTTPS origin or localhost HTTP, with no path, credentials or query');return url.origin;
}
export async function packageConnector(base,output){const origin=connectorBase(base),destination=resolve(output);if(!destination.endsWith('.zip'))throw Error('Output must be .zip');
 const entries=[];for(const name of connectorFiles){const bytes=await readFile(join(source,name));if(name.endsWith('.json')){const text=bytes.toString('utf8').replaceAll('${VISIT_BASE_URL}',origin);JSON.parse(text);entries.push({name,bytes:Buffer.from(text)});}else entries.push({name,bytes});}
 const require=createRequire(join(root,'apps/server/package.json')),archiver=require('archiver');await mkdir(dirname(destination),{recursive:true});const temporary=destination+'.tmp';
 const stream=createWriteStream(temporary,{flags:'wx',mode:0o600}),done=finished(stream);void done.catch(()=>{});const archive=archiver('zip',{zlib:{level:6}});archive.on('error',error=>stream.destroy(error));archive.pipe(stream);
 try{for(const entry of entries)archive.append(entry.bytes,{name:'visit-settle/'+entry.name,date:new Date('2026-01-01T00:00:00Z')});await archive.finalize();await done;await rename(temporary,destination);return {file:destination,baseUrl:origin,files:entries.map(entry=>entry.name)};}
 catch(error){stream.destroy();await done.catch(()=>{});await unlink(temporary).catch(()=>{});throw error;}
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 try{const args=process.argv.slice(2);if(args.length!==4||args[0]!=='--base-url'||args[2]!=='--output')throw Error('Use --base-url ORIGIN --output PATH.zip');console.log(JSON.stringify(await packageConnector(args[1],args[3])));}catch(error){console.error(error.message);process.exitCode=1;}
}
