/** Original station identity normalization; distinct from settlement/store name rules. */
export function stationAssetName(value:string){return value.normalize('NFKC').replace(/[\s\u0085\u001c-\u001f]/g,'').replaceAll('ケ','ヶ').replaceAll('ｹ','ヶ');}
