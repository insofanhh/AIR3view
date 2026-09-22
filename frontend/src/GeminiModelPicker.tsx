import { useEffect, useState } from 'react';

type ModelOption = {id:string;name:string;description:string};
type Props = {value:string;onChange:(value:string)=>void;configured:boolean;keyVersion:number;pendingKey:boolean};

export function GeminiModelPicker({value,onChange,configured,keyVersion,pendingKey}:Props){
  const [models,setModels]=useState<ModelOption[]>([]);
  const [loading,setLoading]=useState(false),[loaded,setLoaded]=useState(false),[error,setError]=useState('');
  const [reload,setReload]=useState(0),[manual,setManual]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    setModels([]);setLoaded(false);setError('');setLoading(false);
    if(!configured)return ()=>controller.abort();
    setLoading(true);
    fetch('/api/gemini/models',{method:'POST',headers:{'X-AIR3view':'studio'},signal:controller.signal})
      .then(async response=>{
        const data=await response.json();
        if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Không tải được danh sách model.');
        if(!Array.isArray(data.models))throw new Error('Danh sách model không hợp lệ.');
        return data.models as ModelOption[];
      })
      .then(items=>{if(!controller.signal.aborted){setModels(items);setLoaded(true);}})
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e));})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[configured,keyVersion,reload]);
  const normalized=value.replace(/^models\//,'');
  const selected=models.find(model=>model.id===normalized);
  const recommended=models.find(model=>['gemini-3.1-flash-lite','gemini-3.5-flash-lite','gemini-2.5-flash-lite'].includes(model.id));
  return <div className="gemini-model-picker">
    <label className="field"><span>Model Gemini</span><select aria-label="Model Gemini" value={normalized} disabled={!loaded||loading||pendingKey} onChange={e=>onChange(e.target.value)}>
      <option value="" disabled>{loading?'Đang tải model…':'Chọn model từ API key…'}</option>
      {normalized&&!selected&&<option value={normalized}>{normalized} · model đã nhập</option>}
      {models.map(model=><option key={model.id} value={model.id}>{model.name} · {model.id}</option>)}
    </select></label>
    <button className="secondary" type="button" disabled={!configured||loading||pendingKey} onClick={()=>setReload(value=>value+1)}>{loading?'Đang tải…':'Tải lại danh sách model'}</button>
    {recommended&&<button className="text-button model-recommendation" type="button" disabled={loading||pendingKey||normalized===recommended.id} onClick={()=>onChange(recommended.id)}>Dùng {recommended.name || recommended.id} (khuyến nghị)</button>}
    <p className="help-text" role="status">{pendingKey?'Bấm Lưu key trên máy để dùng key mới.':!configured?'Nhập API key rồi bấm Lưu key trên máy để tải model.':loaded?(models.length?`${models.length} model hỗ trợ generateContent do Google trả về.`:'Key không trả model nào hỗ trợ generateContent.'):'Danh sách được tải trực tiếp từ Google.'}</p>
    {error&&<p className="help-text model-error" role="alert">{error}</p>}
    {selected?.description&&<p className="help-text model-description">{selected.description}</p>}
    <button className="text-button" type="button" onClick={()=>setManual(value=>!value)}>{manual?'Ẩn nhập thủ công':'Nhập model thủ công'}</button>
    {manual&&<label className="field"><span>Model ID thủ công</span><input aria-label="Model ID thủ công" value={value} onChange={e=>onChange(e.target.value)} placeholder="gemini-2.5-flash"/></label>}
  </div>;
}
