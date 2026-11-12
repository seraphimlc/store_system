import {CN2JP,LINE_ZH,ZH2JP_ALIAS,LINE_ZH_ALIAS} from './station-cjk-data';
const reverse:Record<string,string>={};for(const [zh,jp]of Object.entries(CN2JP))if(!(jp in reverse))reverse[jp]=zh;
Object.assign(reverse,{'渋':'涩','浜':'滨','桜':'樱','葉':'叶','線':'线','東':'东','鉄':'铁','車':'车','馬':'马','島':'岛'});
export function stationJapanese(value:string){const text=value.trim();return ZH2JP_ALIAS[text]??[...text].map(char=>CN2JP[char]??char).join('');}
export function stationChinese(value:string){return LINE_ZH_ALIAS[value]??[...value].map(char=>reverse[char]??char).join('');}
export function stationLineChinese(value:string){return LINE_ZH[value]||stationChinese(value);}
export function stationSearchVariants(value:string){const text=value.trim();return text?[...new Set([stationJapanese(text),text,stationChinese(text)].filter(Boolean))]:[];}
