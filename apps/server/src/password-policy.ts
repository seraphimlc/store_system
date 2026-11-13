import {BusinessError} from './errors';
export const knownDefaultPassword=(value:string)=>/^(demo123|admin|password|123456)$/i.test(value);
export function rejectDefaultPassword(value:string){if(knownDefaultPassword(value))throw new BusinessError('WEAK_PASSWORD','不能使用已知默认口令，请设置个人密码或生成独立临时口令',400);}
