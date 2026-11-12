import {createHash,randomUUID} from 'node:crypto';
import {mkdir,writeFile,link,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import sharp from 'sharp';
import {BusinessError} from './errors';
import {uploadRoot} from './private-files';
export const PHOTO_BYTES=5*1024*1024;
export const PHOTO_COUNT=3;
const PIXELS=24_000_000;
const digest=(value:string|Buffer)=>createHash('sha256').update(value).digest('hex');
export const photoScope=(storeId:string)=>digest(storeId);
export const photoRef=(scope:string,key:string)=>`/api/batch-work/photos/${scope}/${key}`;
export const photoPath=(scope:string,key:string)=>join(uploadRoot(),'batch-photos',scope,key+'.jpg');
let decoding=false;
export async function prepareBatchPhotos(storeId:string,files:Buffer[]){
 if(!files.length)return [];
 if(decoding)throw new BusinessError('PHOTO_BUSY','照片处理繁忙，请稍后重试',503);
 decoding=true;
 try{
  const scope=photoScope(storeId),dir=join(uploadRoot(),'batch-photos',scope),refs:string[]=[],validated:Buffer[]=[];
  for(const file of files){
   if(!file.length||file.length>PHOTO_BYTES)throw new BusinessError('PHOTO_INVALID','每张照片须不超过 5 MB',400);
   let bytes:Buffer;
   try{
    const image=sharp(file,{limitInputPixels:PIXELS,failOn:'warning'}),meta=await image.metadata();
    if(!meta.format||!['jpeg','png','webp'].includes(meta.format)||!meta.width||!meta.height||meta.width*meta.height>PIXELS)throw Error('image format');
    bytes=await image.rotate().resize(1920,1920,{fit:'inside',withoutEnlargement:true}).flatten({background:'#ffffff'}).jpeg({quality:85,mozjpeg:true}).toBuffer();
   }catch{throw new BusinessError('PHOTO_INVALID','请选择有效的 JPEG、PNG 或 WebP 照片（不超过 2400 万像素）',400);}
   validated.push(bytes);
  }
  for(const bytes of validated){
   const key=digest(bytes),path=photoPath(scope,key),temp=join(dir,'.'+randomUUID());
   await mkdir(dir,{recursive:true,mode:0o700});
   try{await writeFile(temp,bytes,{flag:'wx',mode:0o600});await link(temp,path).catch(error=>{if(error.code!=='EEXIST')throw error;});}
   finally{await unlink(temp).catch(()=>{});}
   refs.push(photoRef(scope,key));
  }
  return refs;
 }finally{decoding=false;}
}
