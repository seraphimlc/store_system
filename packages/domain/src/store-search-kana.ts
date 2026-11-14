import {normalizeStoreQuery} from './store-search';
export const KANA_CANDIDATE_VERSION='existing-kana-fold-v1';
/** Existing kana glyphs only. No kanji reading, romanization, aliases or identity inference.
 * Basic hiragana U+3041..U+3096 correspond to U+30A1..U+30F6 in Unicode charts.
 * Iteration signs and exceptional voiced kana deliberately stay distinct.
 */
export function existingKanaKey(value:string){return normalizeStoreQuery(value).replace(/[\u3041-\u3096]/gu,c=>String.fromCodePoint(c.codePointAt(0)!+0x60));}
/** A deterministic alternate written form, for derived tests only. Not a user query. */
export function alternateKanaWriting(value:string){return value.normalize('NFKC').replace(/[\u3041-\u3096\u30a1-\u30f6]/gu,c=>{const n=c.codePointAt(0)!;return String.fromCodePoint(n<=0x3096?n+0x60:n-0x60);});}
