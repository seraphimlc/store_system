let pending:Promise<typeof google.maps>|undefined;
let activeKey='',authFailedKey='';
const authListeners=new Set<()=>void>();
let authHandlerInstalled=false;
function installAuthHandler(){if(authHandlerInstalled)return;authHandlerInstalled=true;const target=window as unknown as Record<string,unknown>,previous=target.gm_authFailure;target.gm_authFailure=()=>{authFailedKey=activeKey;for(const listener of authListeners)listener();if(typeof previous==='function')previous();};}
export function onGoogleMapsAuthFailure(listener:()=>void){installAuthHandler();authListeners.add(listener);return()=>{authListeners.delete(listener);};}
export function googleMaps(key:string,language:string){
 installAuthHandler();activeKey=key;
 // Google may report auth failure only once per document; don't silently reuse that SDK on the next page.
 if(authFailedKey===key)return Promise.reject(Error('MAP_AUTH_FAILED'));
 if(typeof google!=='undefined'&&google.maps?.Map&&google.maps?.Data)return Promise.resolve(google.maps);
 if(pending)return pending;
 pending=new Promise<typeof google.maps>((resolve,reject)=>{
  const callback='__storeGoogleMapsReady',target=window as unknown as Record<string,unknown>,script=document.createElement('script');
  const timer=window.setTimeout(()=>fail(),20000);
  function cleanup(){window.clearTimeout(timer);delete target[callback];}
  function fail(){cleanup();script.remove();pending=undefined;reject(Error('MAP_SDK_UNAVAILABLE'));}
  target[callback]=()=>{cleanup();if(typeof google!=='undefined'&&google.maps?.Map&&google.maps?.Data)resolve(google.maps);else fail();};
  script.src='https://maps.googleapis.com/maps/api/js?'+new URLSearchParams({key,callback,loading:'async',v:'quarterly',language:language==='ja'?'ja':'zh-CN'});script.async=true;script.onerror=fail;document.head.append(script);
 });return pending;
}
