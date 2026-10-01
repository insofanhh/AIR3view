import { NumberInput } from './NumberInput';
import './reaction-scene-duration.css';

type Props = {settings:Record<string,any>;onChange:(patch:Record<string,any>)=>void};

export function ReactionSceneDuration({settings,onChange}:Props) {
  const ranged=settings.reaction_scene_duration_mode==='range';
  const low=settings.reaction_scene_min_seconds??10, high=settings.reaction_scene_max_seconds??20;
  return <fieldset className="reaction-scene-duration">
    <legend>Thời lượng mỗi cảnh</legend>
    <label>Chế độ thời lượng cảnh<select aria-label="Thời lượng mỗi cảnh" value={ranged?'range':'auto'}
      onChange={e=>onChange({reaction_scene_duration_mode:e.target.value,
        ...(e.target.value==='range'?{reaction_scene_min_seconds:Math.min(low,high),reaction_scene_max_seconds:Math.max(low,high)}:{})})}>
      <option value="auto">Tự động · giữ cách dựng hiện tại</option>
      <option value="range">Theo khoảng thời lượng</option>
    </select></label>
    {ranged&&<div className="reaction-scene-duration-pair">
      <label>Tối thiểu (giây)<NumberInput aria-label="Thời lượng cảnh tối thiểu" min={1} max={25} step={0.5}
        value={low} onChange={value=>onChange({reaction_scene_min_seconds:value,reaction_scene_max_seconds:Math.max(high,value)})}/></label>
      <label>Tối đa (giây)<NumberInput aria-label="Thời lượng cảnh tối đa" min={1} max={25} step={0.5}
        value={high} onChange={value=>onChange({reaction_scene_max_seconds:value,reaction_scene_min_seconds:Math.min(low,value)})}/></label>
    </div>}
    <p>Cảnh là đoạn nguồn liên tục; một commentary có thể đọc xuyên nhiều cảnh ngắn. Hook dùng thời lượng riêng. {ranged
      ?'Ưu tiên khoảng đã chọn, cho phép lệch để giữ hội thoại trọn vẹn. Không tăng số commentary hoặc kéo tốc độ giọng. Đổi khoảng cần phân tích lại.'
      :'Chọn diễn biến và lời bình theo quy trình Reaction COPS hiện tại.'}</p>
  </fieldset>;
}
