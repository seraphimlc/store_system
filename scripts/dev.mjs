/** One command for source development. No build, database mutation, Docker or deploy command. */
import {spawn} from 'node:child_process';
import {readFile,mkdir,writeFile,rm} from 'node:fs/promises';
import {createServer} from 'node:net';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {developmentEnvironment} from './dev-lib.mjs';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const text=async name=>{try{return await readFile(join(root,name),'utf8');}catch(e){if(e.code==='ENOENT')return '';throw e;}};
const config=developmentEnvironment(root,await text('.env'),await text('.env.next.local'));
const tsx=join(root,'node_modules/tsx/dist/cli.mjs'),children=[];let stopping=false,failed=false,ownsPid=false;
async function free(port){await new Promise((resolve,reject)=>{const server=createServer();server.once('error',()=>reject(Error(`Port ${port} is occupied; existing services were not stopped`)));server.listen(port,'127.0.0.1',()=>server.close(resolve));});}
function launch(name,args,env,cwd=root){const child=spawn(process.execPath,args,{cwd,env,stdio:['ignore','pipe','pipe'],detached:true});const closed=new Promise(resolve=>child.once('exit',resolve));children.push({child,closed});for(const stream of [child.stdout,child.stderr])stream.on('data',bytes=>process.stdout.write(`[${name}] ${bytes}`));child.once('error',()=>{console.error(`[dev] ${name} failed to start`);failed=true;void stop();});child.once('exit',code=>{if(!stopping){console.error(`[dev] ${name} exited (${code}); stopping development services`);failed=true;void stop();}});}
function signal(child,name){try{process.kill(-child.pid,name);}catch(e){if(e.code!=='ESRCH')throw e;}}
async function stop(){if(stopping)return;stopping=true;for(const {child}of children)signal(child,'SIGTERM');await Promise.all(children.map(async({child,closed})=>{let timer;await Promise.race([closed,new Promise(resolve=>{timer=setTimeout(()=>{signal(child,'SIGKILL');resolve();},6000);timer.unref();})]);clearTimeout(timer);}));if(ownsPid)await rm(join(config.directory,'dev.pid'),{force:true});process.exitCode=failed?1:0;}
for(const name of ['SIGINT','SIGTERM'])process.on(name,()=>void stop());
try{
 for(const port of [config.port,8001,8002])await free(port);
 await mkdir(config.directory,{recursive:true,mode:0o700});await writeFile(join(config.directory,'dev.pid'),String(process.pid),{mode:0o600});ownsPid=true;
 launch('api',[tsx,'watch','--clear-screen=false','apps/server/src/main.ts'],config.backend);
 launch('worker',[tsx,'watch','--clear-screen=false','--include','apps/server/src/**/*.ts','--include','packages/domain/src/**/*.ts','apps/worker/src/main.ts'],config.backend);
 launch('admin',[join(root,'apps/admin/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','8001','--strictPort'],config.frontend,join(root,'apps/admin'));
 launch('staff',[join(root,'apps/staff/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','8002','--strictPort'],config.frontend,join(root,'apps/staff'));
 console.log(`[dev] Admin: ${config.urls.admin}\n[dev] Staff: ${config.urls.staff}\n[dev] API: ${config.urls.api}\n[dev] Ctrl+C stops all four services. Config changes require restarting pnpm dev.`);
 console.log('[dev] Unified local store_settle DB retained; private dev task directory; historical DB task reconciliation disabled.');
}catch(error){console.error('[dev] '+error.message);failed=true;await stop();}
