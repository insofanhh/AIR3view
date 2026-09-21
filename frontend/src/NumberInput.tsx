import { useEffect, useRef, useState } from 'react';
import { Minus, Plus } from 'lucide-react';

type Props = {
  value:number; onChange:(value:number)=>void; 'aria-label':string;
  min?:number; max?:number; step?:number; disabled?:boolean; integer?:boolean;
};

export function NumberInput({value,onChange,min,max,step=1,disabled=false,integer=false,'aria-label':label}:Props) {
  const format=(n:number)=>String(Number(n.toFixed(6)));
  const [draft,setDraft]=useState(()=>format(value));
  const editing=useRef(false), changed=useRef(false);
  useEffect(()=>{if(!editing.current){setDraft(format(value));changed.current=false;}},[value]);
  const clamp=(n:number)=>Math.min(max??Infinity,Math.max(min??-Infinity,integer?Math.round(n):n));
  const read=()=>draft.trim()!==''&&Number.isFinite(Number(draft.replace(',','.'))) ? Number(draft.replace(',','.')) : value;
  const publish=(n:number)=>{const next=clamp(n);setDraft(format(next));changed.current=false;if(next!==value)onChange(next);};
  const commit=()=>{editing.current=false;if(changed.current)publish(read());};
  const adjust=(direction:number)=>publish(Number((read()+direction*step).toFixed(6)));
  return <div className="number-input">
    <input type="text" inputMode={integer?'numeric':'decimal'} role="spinbutton" aria-label={label}
      aria-valuenow={value} aria-valuemin={min} aria-valuemax={max} disabled={disabled} value={draft}
      onFocus={()=>{editing.current=true;}} onChange={e=>{changed.current=true;setDraft(e.target.value);}}
      onBlur={commit} onKeyDown={e=>{
        if(e.key==='Enter'){e.preventDefault();e.currentTarget.blur();}
        if(e.key==='Escape'){setDraft(format(value));changed.current=false;e.currentTarget.blur();}
        if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();adjust(e.key==='ArrowUp'?1:-1);}
      }}/>
    <button type="button" aria-label={'Giảm '+label} title={'Giảm '+label} disabled={disabled||(min!==undefined&&read()<=min)} onMouseDown={e=>e.preventDefault()} onClick={()=>adjust(-1)}><Minus size={14}/></button>
    <button type="button" aria-label={'Tăng '+label} title={'Tăng '+label} disabled={disabled||(max!==undefined&&read()>=max)} onMouseDown={e=>e.preventDefault()} onClick={()=>adjust(1)}><Plus size={14}/></button>
  </div>;
}
