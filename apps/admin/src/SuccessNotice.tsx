import {useEffect,useRef,type ComponentProps} from 'react';import type {Alert} from '@appica/ui-react/alert';import {useToastManager} from '@appica/ui-react/toast';
/** Successful actions announce once via Toast and occupy no page layout space. */
export function SuccessNotice(props:ComponentProps<typeof Alert>){const toast=useToastManager(),last=useRef<unknown>(null);useEffect(()=>{if(props.children&&last.current!==props.children){last.current=props.children;toast.add({title:props.children,type:'success'});}},[props.children,toast]);return null;}
