import {createHmac,timingSafeEqual} from 'node:crypto';
interface SuggestionReceipt {purpose?:'similarity'|'completion';actorId:number;gridId:number;q:string;state:string;businessStatus:string;page:number;names:string[];expires:number}
export function signSuggestionReceipt(value:Omit<SuggestionReceipt,'expires'>,secret:string){const payload=Buffer.from(JSON.stringify({...value,expires:Date.now()+15*60*1000})).toString('base64url');return payload+'.'+createHmac('sha256',secret).update(payload).digest('hex');}
export function verifySuggestionReceipt(token:string,expected:Omit<SuggestionReceipt,'expires'|'names'> & {name:string},secret:string){
 try{const [payload,signature,extra]=token.split('.');if(extra||!payload||!signature)return false;const actual=Buffer.from(signature,'hex'),wanted=createHmac('sha256',secret).update(payload).digest();if(actual.length!==wanted.length||!timingSafeEqual(actual,wanted))return false;
  const value=JSON.parse(Buffer.from(payload,'base64url').toString()) as SuggestionReceipt;
  return (value.purpose??'similarity')===(expected.purpose??'similarity')&&Number.isFinite(value.expires)&&value.expires>Date.now()&&value.actorId===expected.actorId&&value.gridId===expected.gridId&&value.q===expected.q&&value.state===expected.state&&value.businessStatus===expected.businessStatus&&value.page===expected.page&&Array.isArray(value.names)&&value.names.includes(expected.name);
 }catch{return false;}
}
