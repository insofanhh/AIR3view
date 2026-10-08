import { NumberInput } from './NumberInput';
import './reaction-scene-duration.css';

type Props = {settings:Record<string,any>;onChange:(patch:Record<string,any>)=>void};

export function ReactionAudioMode({settings,onChange}:Props) {
  const dubbed=settings.reaction_audio_mode==='dubbed';
  if(settings.reaction_commentary_count===0) return <fieldset className="reaction-scene-duration"><legend>Full review · AI dẫn chuyện toàn video</legend><p>AI tổng hợp tình huống, kiểm chứng và phân tích nguyên nhân–hệ quả theo chứng cứ. Tắt toàn bộ tiếng và phụ đề thoại gốc, kể cả hook. Dùng cùng giọng/tốc độ; một đoạn lời kể có thể chạy xuyên nhiều cảnh hình. Đặt 1–10 để dùng các điểm commentary và lựa chọn hội thoại nhân vật.</p></fieldset>;
  return <fieldset className="reaction-scene-duration">
    <legend>Hội thoại nhân vật</legend>
    <label>Âm thanh hội thoại<select aria-label="Âm thanh hội thoại Reaction COPS" value={dubbed?'dubbed':'original'}
      onChange={e=>onChange({reaction_audio_mode:e.target.value})}>
      <option value="dubbed">AI lồng toàn bộ hội thoại · ngôn ngữ đầu ra</option>
      <option value="original">Giữ tiếng hiện trường gốc</option>
    </select></label>
    <p>{dubbed?'Thoại nhân vật giữ đúng nghĩa, câu hỏi–đáp và phủ định; phụ đề có nhãn người nói khi có căn cứ. Commentary là các điểm phân tích riêng, không tính lượt lồng tiếng. Tắt toàn bộ âm thanh gốc, kể cả tiếng nền, để tránh chồng lời; dùng nhạc nền riêng nếu cần.'
      :'Hội thoại hiện trường giữ tiếng gốc; commentary dùng giọng AI đã chọn.'} Hook mặc định tắt; khi bật vẫn chọn highlight như hiện tại và lồng tiếng theo chế độ này. Đổi chế độ cần phân tích lại.</p>
  </fieldset>;
}

export function ReactionSceneDuration({settings,onChange}:Props) {
  const ranged=settings.reaction_scene_duration_mode==='range';
  const low=settings.reaction_scene_min_seconds??10, high=settings.reaction_scene_max_seconds??20;
  return <fieldset className="reaction-scene-duration">
    <legend>Thời lượng mỗi cảnh</legend>
    <label>Chế độ thời lượng cảnh<select aria-label="Thời lượng mỗi cảnh" value={ranged?'range':'auto'}
      onChange={e=>onChange({reaction_scene_duration_mode:e.target.value,
        ...(e.target.value==='range'?{reaction_scene_min_seconds:Math.min(low,high),reaction_scene_max_seconds:Math.max(low,high)}:{})})}>
      <option value="auto">{settings.reaction_commentary_count===0?'Tự động · hình 4–6 giây, lời kể xuyên nhiều cảnh':'Tự động · giữ cách dựng hiện tại'}</option>
      <option value="range">Theo khoảng thời lượng</option>
    </select></label>
    {ranged&&<div className="reaction-scene-duration-pair">
      <label>Tối thiểu (giây)<NumberInput aria-label="Thời lượng cảnh tối thiểu" min={1} max={25} step={0.5}
        value={low} onChange={value=>onChange({reaction_scene_min_seconds:value,reaction_scene_max_seconds:Math.max(high,value)})}/></label>
      <label>Tối đa (giây)<NumberInput aria-label="Thời lượng cảnh tối đa" min={1} max={25} step={0.5}
        value={high} onChange={value=>onChange({reaction_scene_max_seconds:value,reaction_scene_min_seconds:Math.min(low,value)})}/></label>
    </div>}
    <p>Cảnh hình là đoạn của một shot nguồn thật; một commentary có thể đọc xuyên nhiều cảnh ngắn. Hook dùng thời lượng riêng. {ranged
      ?'Mỗi cảnh hình phải đạt khoảng đã chọn ở 30 fps. Không chia đôi một cảnh rồi nối lại; các diễn biến riêng trong cùng góc quay chỉ được chọn khi có dẫn chứng khác và khoảng nguồn được bỏ qua. Tool kiểm tra hình trước khi viết lời; nếu thiếu ít, tự cân cửa sổ và viết lại riêng đoạn bị ảnh hưởng, giữ giọng/tốc độ. Đổi khoảng ưu tiên dùng lại lời và audio đã đạt. Nếu nguồn không đủ để cân an toàn, tool báo rõ. Xuất theo phần có thể cắt ngắn cảnh tại mép file.'
      :settings.reaction_commentary_count===0?'Full review dùng hình 4–6 giây khi đủ nguồn; ranh giới có thể ngắn hơn. Cửa sổ voice được gộp để giữ mạch kể và tốc độ.':'Chọn diễn biến và lời bình theo quy trình Reaction COPS hiện tại.'}</p>
  </fieldset>;
}
