import type {Session} from '../../contracts/src';
export const SESSION_EXPIRED_EVENT='store:session-expired';
export class ApiFailure extends Error{constructor(public code:string,message:string){super(message);}}
const japanese:Record<string,string>={UNAUTHENTICATED:'ログインの有効期限が切れました。再度ログインしてください。',INVALID_CREDENTIALS:'アカウントまたはパスワードが正しくないか、現在利用できません。',FORBIDDEN:'この操作を行う権限がありません。',VALIDATION:'入力内容と日付・件数を確認してください。',CSRF_INVALID:'送信の検証に失敗しました。画面を更新してください。',PASSWORD_CHANGE_REQUIRED:'初期パスワードを変更してください。',PASSWORD_INCORRECT:'現在のパスワードが正しくありません。',CODE_EXISTS:'スタッフ番号は既に登録されています。',USERNAME_EXISTS:'ログイン名は既に使用されています。',CONFLICT:'データが更新されました。画面を更新して再度お試しください。',ALREADY_SUBMITTED:'本日は申告済みです。編集画面をご利用ください。',REPORT_LOCKED:'照合済みのため変更できません。',PLAN_WINDOW_CLOSED:'予定の登録期間外です。',PLAN_ALL_LOCKED:'変更可能な日付がありません。',REPORT_AI_DISABLED:'AIレポートは無効です。数値の比較は利用できます。',REPORT_NOT_RETRYABLE:'生成中または完了済みのため再試行できません。',RECIPIENT_FORBIDDEN:'宛先が現在の送信範囲外です。',AUTH_BUSY:'ログインが混み合っています。しばらくして再度お試しください。'};
function message(code:string,fallback:string){return typeof document!=='undefined'&&document.documentElement.lang.startsWith('ja')?japanese[code]??'リクエストに失敗しました。画面を更新して再度お試しください。':fallback;}
export async function api<T>(path:string,session?:Session|null,body?:unknown):Promise<T>{
 let response:Response;
 try{response=await fetch('/api'+path,{credentials:'same-origin',method:body===undefined?'GET':'POST',headers:{...(body===undefined?{}:{'Content-Type':'application/json'}),...(session?{'X-CSRF-Token':session.csrfToken}:{})},body:body===undefined?undefined:JSON.stringify(body)});}
 catch{throw new ApiFailure('NETWORK',message('NETWORK','连接失败，请稍后重试'));}
 if(response.status===401&&path!=='/auth/login'&&typeof window!=='undefined')window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
 let data:unknown;try{data=await response.json();}catch{throw new ApiFailure('INVALID_RESPONSE',message('INVALID_RESPONSE','服务响应异常，请稍后重试'));}
 if(!response.ok){const error=(data??{})as{code?:string;message?:string};const code=error.code||'NETWORK';throw new ApiFailure(code,message(code,error.message||'请求失败，请重试'));}
 return data as T;
}
/** Multipart uploads intentionally leave Content-Type to the browser so the boundary is correct. */
export async function upload<T>(path:string,session:Session,form:FormData):Promise<T>{
 let response:Response;try{response=await fetch('/api'+path,{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':session.csrfToken},body:form});}catch{throw new ApiFailure('NETWORK',message('NETWORK','上传连接失败，请稍后重试'));}
 if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
 let data:unknown;try{data=await response.json();}catch{throw new ApiFailure('INVALID_RESPONSE',message('INVALID_RESPONSE','上传响应异常，请刷新文件列表核对'));}
 if(!response.ok){const error=data as{code?:string;message?:string};throw new ApiFailure(error.code||'UPLOAD_FAILED',message(error.code||'UPLOAD_FAILED',error.message||'上传失败'));}
 return data as T;
}
