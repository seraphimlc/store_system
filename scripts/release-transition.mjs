/** Release transitions with explicit I/O: used by the deployment CLI and isolated verification. */
export async function replaceGateway({read,write,configuration,validate,reload,health,delay=()=>new Promise(resolve=>setTimeout(resolve,250)),attempts=6}){
 const previous=await read();
 try{
  await write(configuration);await validate();await reload();
  let failure;
  for(let attempt=0;attempt<attempts;attempt++){
   try{await health();return;}catch(error){failure=error;if(attempt+1<attempts)await delay();}
  }
  throw failure;
 }catch(error){
  try{await write(previous);await validate();await reload();}catch(recovery){throw new AggregateError([error,recovery],'Gateway failed and previous configuration could not be restored');}
  throw error;
 }
}

export async function handoffRelease({stopOldWorker,verifyTasks,switchGateway,startWorker,stopWorker,restoreGateway,startOldWorker}){
 try{
  await stopOldWorker();await verifyTasks();await switchGateway();await startWorker();
 }catch(error){
  const failures=[error];let candidateStopped=false;
  try{await stopWorker();candidateStopped=true;}catch(recovery){failures.push(recovery);}
  // An unsuccessful candidate stop must not prevent restoring HTTP traffic.
  try{await restoreGateway();}catch(recovery){failures.push(recovery);}
  // Never intentionally run both workers against the persistent job volume.
  if(candidateStopped){try{await startOldWorker();}catch(recovery){failures.push(recovery);}}
  if(failures.length>1)throw new AggregateError(failures,'Release failed; recovery requires attention');
  throw error;
 }
}
