import { NumberInput } from './NumberInput';
import { durationBudget, ratioFromMinutes, budgetSummary, budgetTime } from './durationBudget';

type Props = {settings:Record<string,any>;sourceSeconds?:number;onChange:(name:string,value:number)=>void};
export function DurationControls({settings:s,sourceSeconds,onChange}:Props) {
  const b=durationBudget(s,sourceSeconds);
  const minMinutes=b.minimum===null?0:b.minimum/60;
  return <div className="duration-controls">
    {s.output_mode==='single'&&<label className="field"><span>Thời lượng video mong muốn (phút)</span>
      <NumberInput aria-label="Thời lượng video mong muốn (phút)" value={(s.summary_seconds??180)/60} min={0} max={30} step={.5}
        onChange={value=>onChange('summary_seconds',value===0?0:Math.max(10,Math.round(value*60*1000)/1000))}/>
      <small>0 = theo video gốc. Đổi mục tiêu giữ nguyên tỷ lệ và tự tính lại số phút tối thiểu.</small>
    </label>}
    {s.output_mode==='parts'&&<div className="field-pair">
      <label className="field"><span>Số phần</span><NumberInput integer aria-label="Số phần" min={1} max={100} value={b.count} onChange={v=>onChange('part_count',v)}/></label>
      <label className="field"><span>Giây / phần</span><NumberInput aria-label="Giây / phần" min={30} max={1800} value={s.part_seconds??60} onChange={v=>onChange('part_seconds',v)}/></label>
    </div>}
    {!b.legacy&&<>
      <label className="field"><span>Mức tối thiểu · {Number((b.ratio*100).toFixed(2))}%</span>
        <input aria-label="Mức tối thiểu (%)" type="range" min=".6" max="1" step=".01" value={b.ratio} onChange={e=>onChange('duration_min_ratio',Number(e.target.value))}/>
      </label>
      <label className="field"><span>{b.count>1?'Thời lượng tối thiểu mỗi phần (phút)':'Thời lượng tối thiểu (phút)'}</span>
        <NumberInput aria-label="Thời lượng tối thiểu (phút)" min={b.basis===null?0:Math.max(10,b.basis*.6)/60}
          max={b.basis===null?0:Math.max(10,b.basis)/60} step={.1} value={minMinutes} disabled={!b.minimumEditable}
          onChange={v=>onChange('duration_min_ratio',ratioFromMinutes(s,sourceSeconds,v))}/>
        <small>Nhập số phút tối thiểu sẽ tự đổi %. Khoảng cho phép: 60–100% thời lượng khả dụng; mỗi phần ít nhất 10 giây.</small>
      </label>
    </>}
    {b.legacy&&<p className="help-text">Bản cũ dùng mức tối thiểu 75%. Chọn quy trình “Lập kế hoạch trước” để điều chỉnh.</p>}
    <div className="output-summary" role="status" aria-live="polite">{budgetSummary(s,sourceSeconds)}</div>
    {b.automatic&&b.source!==null&&<p className="help-text">Theo video gốc: {budgetTime(b.source)}. Giá trị 0 được giữ khi lưu.</p>}
    {b.limitedBySource&&<p className="help-text">Nguồn chỉ có {budgetTime(b.source)}. Mức tối thiểu tính theo {budgetTime(b.basis)}{b.count>1?' nguồn/phần':' nguồn khả dụng'}; {budgetTime(b.target)} là giới hạn tối đa, không thêm hình lặp để lấp thời gian.</p>}
    {b.provisional&&<p className="help-text">Khoảng thời lượng tạm tính; sẽ cập nhật khi chuẩn bị nguồn xong.</p>}
    {b.impossible&&<p className="duration-warning" role="alert">Nguồn hoặc mục tiêu quá ngắn cho số phần này. Mỗi phần cần ít nhất 10 giây; giảm số phần hoặc chọn nguồn dài hơn.</p>}
    <p className="help-text">Đây là khoảng yêu cầu, tính cả hook và thoại gốc; không phải dự đoán độ dài kịch bản AI. Đổi thiết lập cần phân tích lại.</p>
  </div>;
}
