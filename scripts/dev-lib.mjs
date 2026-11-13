import {parseEnv} from 'node:util';
import {resolve,join} from 'node:path';
/** Same precedence as Node env files: shell > .env.next.local > .env. */
export function developmentEnvironment(root,baseText,localText,shell=process.env){
 const env={...parseEnv(baseText),...parseEnv(localText),...shell};
 let database;try{database=new URL(env.NEXT_DATABASE_URL);}catch{throw Error('Configure NEXT_DATABASE_URL in .env.next.local');}
 if(!['postgres:','postgresql:'].includes(database.protocol)||!['localhost','127.0.0.1','[::1]'].includes(database.hostname)||database.pathname!=='/store_settle')throw Error('Development requires the explicit local store_settle database');
 if((env.NEXT_SESSION_SECRET??'').length<32)throw Error('Configure NEXT_SESSION_SECRET (at least 32 characters)');
 const port=Number(env.NEXT_PORT??3000);if(!Number.isInteger(port)||port<1024||port>65535||[8001,8002].includes(port))throw Error('NEXT_PORT conflicts with a frontend port or is invalid');
 const directory=resolve(root,'data/refactor/dev-jobs');
 Object.assign(env,{NODE_ENV:'development',NEXT_HOST:'127.0.0.1',NEXT_PORT:String(port),NEXT_COOKIE_SECURE:'false',NEXT_TASK_DIRECTORY:directory,NEXT_UPLOAD_DIRECTORY:join(directory,'uploads'),NEXT_WORKER_SOURCE:'true',NEXT_WORKER_RECONCILE:'false'});
 if(env.NEXT_LEGACY_UPLOAD_HOST_DIRECTORY)env.NEXT_LEGACY_UPLOAD_DIRECTORY=env.NEXT_LEGACY_UPLOAD_HOST_DIRECTORY;
 const frontend={};for(const key of ['PATH','HOME','TMPDIR','SystemRoot'])if(shell[key])frontend[key]=shell[key];
 Object.assign(frontend,{NODE_ENV:'development',NEXT_DEV_API_TARGET:`http://127.0.0.1:${port}`});
 return {backend:env,frontend,port,directory,urls:{admin:'http://127.0.0.1:8001/admin/',staff:'http://127.0.0.1:8002/',api:`http://127.0.0.1:${port}/api/health/ready`}};
}
