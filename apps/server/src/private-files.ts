import {BusinessError} from './errors';
import {createHash} from 'node:crypto';import {createReadStream,realpathSync,statSync} from 'node:fs';import {mkdir,link,unlink,stat} from 'node:fs/promises';import {resolve,join,basename,relative,isAbsolute} from 'node:path';
export function uploadRoot(){return resolve(process.env.NEXT_UPLOAD_DIRECTORY||'data/refactor/uploads');}
export function privateUploadPath(path:string){const root=uploadRoot(),resolved=resolve(path),rel=relative(root,resolved);if(!rel||rel.startsWith('..')||rel.includes('../'))throw new Error('Upload path is outside private directory');return resolved;}
export async function storeUpload(path:string){
 const input=privateUploadPath(path);const hash=createHash('sha256');for await(const chunk of createReadStream(input))hash.update(chunk);const sha=hash.digest('hex');const root=uploadRoot();await mkdir(root,{recursive:true,mode:0o700});const target=join(root,sha+'.xlsx');
 try{await link(input,target);}catch(error){if((error as NodeJS.ErrnoException).code!=='EEXIST')throw error;}
 if(input!==target)await unlink(input);return {sha,path:target,size:(await stat(target)).size};
}
export function safeUploadName(value:string){
 let text=value;if([...value].every(character=>character.charCodeAt(0)<=255)){const bytes=Buffer.from(value,'latin1'),decoded=bytes.toString('utf8');if(!decoded.includes('\uFFFD')&&Buffer.from(decoded,'utf8').equals(bytes))text=decoded;}
 const name=basename(text.replaceAll('\\','/'));return name.replace(/[\u0000-\u001f\u007f]/g,'').slice(0,255);
}

/** Resolve only owned uploads or the explicitly-mounted legacy /data/uploads tree for reads. */
export function readUploadPath(path:string){
 if(path==='kept')throw new BusinessError('FILE_MISSING','该记录为历史保留数据，没有独立上传原件；可查看已有结算结果',404);
 if(typeof path!=='string'||path.includes('\0')||path.split(/[\\/]/).includes('..'))throw new BusinessError('FILE_MISSING','原件路径无效或未配置历史文件目录',404);
 let candidate:string,root:string;
 try{candidate=privateUploadPath(path);root=uploadRoot();}
 catch{
  const legacy=process.env.NEXT_LEGACY_UPLOAD_DIRECTORY;
  if(!legacy||!isAbsolute(legacy)||!path.startsWith('/data/uploads/'))throw new BusinessError('FILE_MISSING','原件不在可读目录，请挂载历史文件目录后重试',404);
  root=resolve(legacy);if(root==='/')throw new BusinessError('FILE_MISSING','历史文件目录配置无效',404);candidate=resolve(root,path.slice('/data/uploads/'.length));
 }
 try{
  const actualRoot=realpathSync(root),actual=realpathSync(candidate),rel=relative(actualRoot,actual);
  if(!rel||rel==='..'||rel.startsWith('../')||isAbsolute(rel)||!statSync(actual).isFile())throw new Error('OUTSIDE');
  return actual;
 }catch{throw new BusinessError('FILE_MISSING','原件不存在或超出可读目录，请核对历史文件挂载',404);}
}
