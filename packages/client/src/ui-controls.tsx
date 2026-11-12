import {createContext,createElement,useContext,type ComponentPropsWithRef,type ElementType,type ReactNode} from 'react';
export type ClientControlTag='fieldset'|'form'|'input'|'select'|'textarea'|'button'|'table'|'thead'|'tbody'|'tr'|'th'|'td';
export type ClientControls=Partial<Record<ClientControlTag|'pagination'|'successNotice',ElementType>>;
const Controls=createContext<ClientControls>({});
/** H5 renders the original native elements unless its own host explicitly supplies controls. */
export function ClientControlsProvider({components,children}:{components:ClientControls;children:ReactNode}){return createElement(Controls.Provider,{value:components},children);}
function control<T extends ClientControlTag>(tag:T){return function ClientControl(props:ComponentPropsWithRef<T>){const components=useContext(Controls);return createElement(components[tag]??tag,props);};}
export const UIForm=control('form'),UIInput=control('input'),UISelect=control('select'),UITextarea=control('textarea'),UIButton=control('button'),UITable=control('table'),UITableHeader=control('thead'),UITableBody=control('tbody'),UITableRow=control('tr'),UITableHead=control('th'),UITableCell=control('td');

export const UIFieldset=control('fieldset');

export function UIPagination(props:ComponentPropsWithRef<'div'>){const components=useContext(Controls);return createElement(components.pagination??'div',props);}

/** Hosts choose result feedback; H5 retains its existing inline announcement. */
export function UISuccessNotice(props:ComponentPropsWithRef<'div'>){const components=useContext(Controls);return createElement(components.successNotice??'div',props);}
