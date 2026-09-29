import {useState} from 'react';
import {FolderOpen, LoaderCircle} from 'lucide-react';
import './export-directory.css';

export function ExportDirectoryPicker({directory,defaultDirectory,onChange,disabled=false}:{directory:string;defaultDirectory:string;onChange:(directory:string)=>void;disabled?:boolean}){
  const [choosing,setChoosing]=useState(false),[error,setError]=useState('');
  async function choose(){
    setChoosing(true);setError('');
    try{
      const response=await fetch('/api/export/pick-directory',{method:'POST',headers:{'X-AIR3view':'studio','Content-Type':'application/json'},body:JSON.stringify({initial:directory||defaultDirectory})});
      const result=await response.json();
      if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Không mở được File Explorer.');
      if(result.directory)onChange(result.directory);
    }catch(reason){setError(String(reason));}finally{setChoosing(false);}
  }
  return <div className="export-directory-picker">
    <span>Thư mục lưu video xuất ra</span>
    <div className="export-directory-row"><input aria-label="Thư mục lưu video xuất ra" readOnly value={directory||defaultDirectory} placeholder="Thư mục mặc định" title={directory||defaultDirectory}/><button type="button" className="secondary" disabled={disabled||choosing} onClick={()=>void choose()}>{choosing?<LoaderCircle size={14} className="spin"/>:<FolderOpen size={14}/>} {choosing?'Đang chọn…':'Chọn thư mục…'}</button></div>
    {directory&&<button type="button" className="export-directory-reset" disabled={disabled||choosing} onClick={()=>onChange('')}>Dùng thư mục mặc định</button>}
    {error&&<small role="alert">{error}</small>}
  </div>;
}
