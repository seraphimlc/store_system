import type {ReactNode} from 'react';
import {Drawer,DrawerContent,DrawerHeader,DrawerTitle,DrawerBody} from '@appica/ui-react/drawer';
export function MapDrawer({title,language,onClose,actions,children}:{title:string;language:string;onClose:()=>void;actions?:ReactNode;children:ReactNode}){
 return <Drawer side="right" open modal={false} disablePointerDismissal onOpenChange={open=>{if(!open)onClose();}}><DrawerContent className="map-management-drawer" frame={false} backdrop={false} closeLabel={language==='ja'?'閉じる':'关闭'} viewportProps={{className:'map-management-drawer-viewport'}}><DrawerHeader><DrawerTitle>{title}</DrawerTitle></DrawerHeader>{actions&&<div className="map-drawer-header-actions">{actions}</div>}<DrawerBody>{children}</DrawerBody></DrawerContent></Drawer>;
}
