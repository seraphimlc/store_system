import type {ComponentPropsWithoutRef} from 'react';
import {Button,DatePicker,Popup,Toast} from 'antd-mobile';
import {Attendance,type AttendanceSheetProps,type AttendanceDateProps} from '../../../packages/client/src/Attendance';
import type {Language,Session} from '../../../packages/contracts/src';

function AttendanceButton({className,...props}:ComponentPropsWithoutRef<'button'>){
 return <Button {...props} className={className} color={className?.includes('attendance-primary')?'primary':'default'} size="large"/>;
}
function AttendanceSheet({visible,title,busy,onClose,children}:AttendanceSheetProps){
 return <Popup visible={visible} onMaskClick={()=>{if(!busy)onClose();}} onClose={()=>{if(!busy)onClose();}} showCloseButton={!busy} destroyOnClose bodyClassName="attendance-sheet" bodyStyle={{borderTopLeftRadius:16,borderTopRightRadius:16,maxHeight:'90dvh',overflowY:'auto',maxWidth:600,left:0,right:0,marginInline:'auto'}}>
  <h2 className="attendance-sheet-title">{title}</h2>{children}
 </Popup>;
}
function AttendanceDatePicker({visible,value,language,onConfirm,onClose}:AttendanceDateProps){
 // Picker dates are calendar values, not instants: do not convert through UTC or the device timezone.
 const [year,month,day]=value.split('-').map(Number);
 return <DatePicker destroyOnClose visible={visible} value={new Date(year,month-1,day,12)} min={new Date(2000,0,1)} max={new Date(new Date().getFullYear()+1,11,31)} precision="day" title={language==='ja'?'日付を選択':'选择日期'} confirmText={language==='ja'?'記録を表示':'查看记录'} cancelText={language==='ja'?'キャンセル':'取消'} onClose={onClose} onConfirm={date=>onConfirm([date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-'))}/>
}
export function StaffAttendance(props:{session:Session;language:Language;compact?:boolean}){
 return <Attendance {...props} mobile mobileButton={AttendanceButton} mobileSheet={AttendanceSheet} mobileDatePicker={AttendanceDatePicker} onSuccess={message=>Toast.show({content:message})}/>;
}
