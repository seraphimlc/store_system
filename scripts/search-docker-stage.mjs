/** Explicit stage only. Whitelisted compiled artifacts; never deploys, switches traffic or reads runtime .env. */
import {spawnSync} from 'node:child_process';import {mkdtemp,cp,mkdir,readFile,writeFile,readdir,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,relative} from 'node:path';import {createHash} from 'node:crypto';
const [action,image]=process.argv.slice(2);
if(!['build','test'].includes(action)||!/^store-next-search-acceptance:stage-[a-z0-9-]+$/.test(image??''))throw Error('Usage: search-docker-stage.mjs build|test store-next-search-acceptance:stage-ID');
function run(command,args,options={}){const r=spawnSync(command,args,{stdio:'inherit',...options});if(r.error||r.status!==0)throw Error('Search stage command failed: '+command);}
if(action==='test'){
 const check=spawnSync('docker',['image','inspect',image],{stdio:'ignore'});if(check.status!==0)throw Error('Build an explicit candidate image first; no skipped acceptance');
 run(process.execPath,['scripts/test-isolated.mjs','--file=apps/server/test/store-search-docker.test.ts'],{env:{...process.env,NEXT_SEARCH_DOCKER_IMAGE:image}});
}else{
 if(spawnSync('docker',['image','inspect',image],{stdio:'ignore'}).status===0)throw Error('Candidate tag exists; use a fresh ID instead of overwriting it');
 const directory=await mkdtemp(join(tmpdir(),'store-search-api-stage-'));
 try{
  for(const name of ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','apps/server/package.json']){await mkdir(join(directory,name,'..'),{recursive:true});await cp(name,join(directory,name));}
  for(const app of ['server','worker'])await cp('apps/'+app+'/dist',join(directory,'apps',app,'dist'),{recursive:true});
  await mkdir(join(directory,'zone-evidence'));await cp('deploy-next/api.Dockerfile',join(directory,'Dockerfile'));
  const files=[];async function walk(path){for(const entry of await readdir(path,{withFileTypes:true})){const file=join(path,entry.name);if(entry.isSymbolicLink())throw Error('Symlinks are not accepted in the stage build context');if(entry.isDirectory())await walk(file);else{if(entry.name.startsWith('.env'))throw Error('Runtime secrets cannot enter the stage context');const bytes=await readFile(file);files.push({path:relative(directory,file),sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length});}}}await walk(directory);
  run('docker',['build','--label','com.store-system.acceptance-only=true','-t',image,directory]);
  const inspect=spawnSync('docker',['image','inspect',image,'--format','{{.Id}}'],{encoding:'utf8'});if(inspect.status)throw Error('Image metadata unavailable');
  await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});const report={acceptanceOnly:true,image,imageId:inspect.stdout.trim(),files,compiledArtifactsOnly:true,platform:'local native; not production architecture certification',defaultDockerSwitched:false,productionAccessed:false};const path='data/refactor/search-evaluation/api-image-'+Date.now()+'.json';await writeFile(path,JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify({evidence:path,image}));
 }finally{await rm(directory,{recursive:true,force:true});}
}
