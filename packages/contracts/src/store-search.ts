export interface StoreNameSuggestion {name:string;candidateCount:number}
export interface StoreSuggestionPage {
 receipt?:string;
 status:'ready'|'unsupported_query'|'literal_results'|'scope_too_large';
 query:string;strategyVersion:'name-edit-one-v1';page:number;pageSize:number;
 total:number|null;candidateTotal:number|null;hasMore:boolean;items:StoreNameSuggestion[];
}
export type StoreSearchFeedback =
 | {kind:'opened'|'confirmed';searchLogId:number;storeTaskId:number}
 | {kind:'suggestion_selected';searchLogId:number;suggestedName:string;suggestionPage:number;suggestionReceipt:string}
 | {kind:'completion_selected';searchLogId:number;suggestedName:string;completionReceipt:string};
export interface StoreSearchFeedbackReceipt {accepted:true;eventId:string;duplicate:boolean}
export interface StoreCompletionPage {
 receipt?:string;
 status:'ready'|'unsupported_query'|'scope_too_large';query:string;
 total:number|null;candidateTotal:number|null;items:StoreNameSuggestion[];
}

/** Field-specific server evidence; related candidates are not full query matches. */
export interface StoreSearchMatchEvidence {mode:'literal'|'related';method?:'folded_name'|'weighted_name'|'exact_core';nameTerms:string[];addressTerms:string[];idTerms:string[]}
export type StoreRelatedStatus='ready'|'unsupported_query'|'literal_results'|'scope_too_large'|'filters_excluded';

export interface StoreSearchResultGroup {id:number;name:string|null;count:number;regionTaskId?:number;regionName?:string|null}
