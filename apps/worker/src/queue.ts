/** Durable local-volume queue. Business handlers must be idempotent before registration. */
import { mkdir, open, readFile, rename, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
export interface DurableTask { id: string; kind: string; payload: unknown; attempts: number; createdAt: string }
export class DurableQueue {
  constructor(readonly directory: string) {}
  async init() { for (const state of ['queued','running','done','failed']) await mkdir(join(this.directory,state),{recursive:true,mode:0o700}); }
  async enqueue(kind: string, payload: unknown, id: string = randomUUID()) {
    if (!/^[a-zA-Z0-9-]{1,80}$/.test(id) || !/^[a-z][a-z0-9.]{1,80}$/.test(kind)) throw new Error('Invalid task identity');
    await this.init();
    const task: DurableTask = { id, kind, payload, attempts:0,createdAt:new Date().toISOString() };
    const text=JSON.stringify(task);
    if(Buffer.byteLength(text)>65536)throw new Error('Task payload too large; use a private file reference');
    // One stable ID spans all states; the exclusive ID file prevents duplicate enqueue.
    const identity=await open(join(this.directory,id+'.identity'),'wx',0o600);
    await identity.writeFile(text); await identity.sync(); await identity.close();
    const temporary=join(this.directory,'queued',id+'.tmp');
    await writeFile(temporary,text,{mode:0o600});
    await rename(temporary,join(this.directory,'queued',id+'.json'));
    return id;
  }
  async claim(): Promise<DurableTask | null> {
    await this.init();
    const names=(await readdir(join(this.directory,'queued'))).filter(x=>x.endsWith('.json')).sort();
    for(const name of names){
      const running=join(this.directory,'running',name);
      try { await rename(join(this.directory,'queued',name),running); }
      catch(error) { if((error as NodeJS.ErrnoException).code==='ENOENT')continue; throw error; }
      const task=JSON.parse(await readFile(running,'utf8')) as DurableTask;
      task.attempts++;
      await writeFile(running,JSON.stringify(task),{mode:0o600});
      return task;
    }
    return null;
  }
  async finish(task:DurableTask,result:unknown,failed=false){
    const running=join(this.directory,'running',task.id+'.json');
    await writeFile(running,JSON.stringify({...task,finishedAt:new Date().toISOString(),result}),{mode:0o600});
    await rename(running,join(this.directory,failed?'failed':'done',task.id+'.json'));
  }
  async recover(supportedKinds:ReadonlySet<string>){
    await this.init();
    // Called only after acquiring the single-worker lock. No parallel worker may recover live work.
    // An acknowledged publish is reconstructed if a crash occurred after durable identity write.
    for(const name of await readdir(this.directory)){
      if(!name.endsWith('.identity'))continue;
      const id=name.slice(0,-9);let found=false;
      for(const state of ['queued','running','done','failed']){
        try{await readFile(join(this.directory,state,id+'.json'));found=true;break;}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;}
      }
      if(!found){
        const bytes=await readFile(join(this.directory,name),'utf8');
        try{JSON.parse(bytes);}catch{continue;}
        await writeFile(join(this.directory,'queued',id+'.json'),bytes,{flag:'wx',mode:0o600});
      }
    }
    for(const name of await readdir(join(this.directory,'running'))){
      if(!name.endsWith('.json'))continue;
      const path=join(this.directory,'running',name);
      const task=JSON.parse(await readFile(path,'utf8')) as DurableTask;
      if(supportedKinds.has(task.kind)&&task.attempts<3)await rename(path,join(this.directory,'queued',name));
      else await this.finish(task,{code:'INTERRUPTED_OR_UNSUPPORTED'},true);
    }
  }
}
