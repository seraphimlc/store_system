/** Anchor the non-modal sheet to visible page coordinates, not mobile browser fixed-position heuristics. */
export function placeStoreSheet(popup: HTMLElement, sheet: HTMLElement, viewport: {height:number;pageTop:number}) {
 const host=popup.parentElement;
 if(host){host.style.position='absolute';host.style.top='0';host.style.left='0';host.style.width='100%';}
 const height=Math.max(0,viewport.height);
 sheet.style.setProperty('--store-sheet-height',`${height*.4}px`);
 popup.style.position='absolute';
 popup.style.bottom='auto';
 popup.style.top=`${Math.max(viewport.pageTop,viewport.pageTop+height-popup.offsetHeight)}px`;
}
