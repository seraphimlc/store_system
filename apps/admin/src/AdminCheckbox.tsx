import type {ReactNode} from 'react';
import {Checkbox} from '@appica/ui-react/checkbox';
type Props={checked?:boolean;defaultChecked?:boolean;onChange?:(event:{target:{checked:boolean;name:string;value:string}})=>void;name?:string;value?:string;disabled?:boolean;children?:ReactNode;['aria-label']?:string};
export function AdminCheckbox({checked,defaultChecked,onChange,name,value,disabled,children,'aria-label':label}:Props){return <Checkbox name={name} value={value} checked={checked} defaultChecked={defaultChecked} disabled={disabled} aria-label={label} onCheckedChange={next=>onChange?.({target:{checked:next,name:name??'',value:value??'on'}})}>{children}</Checkbox>;}
