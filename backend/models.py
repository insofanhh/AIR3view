from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class WordTiming(Model):
    text: str = Field(max_length=4000)
    start: float = Field(ge=0)
    end: float = Field(ge=0)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end < self.start:
            raise ValueError('Mốc từ kết thúc phải lớn hơn hoặc bằng mốc bắt đầu.')
        return self


class Cue(Model):
    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=4000)
    speaker: str = 'original'
    words: list[WordTiming] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end <= self.start:
            raise ValueError('Mốc kết thúc phải lớn hơn mốc bắt đầu.')
        return self


class Narration(Model):
    id: str
    start: float = Field(ge=0)
    text: str = Field(max_length=3000)
    enabled: bool = True
    evidence: str = ''
    section: Literal['opening', 'development', 'ending'] = 'development'
    segment_id: str = ''
    part: int = Field(default=1, ge=1, le=100)
    audio: str = ''
    audio_hash: str = ''
    duration: float = 0
    target_duration: float = Field(default=0, ge=0, le=1800)
    caption_version: int = 0
    cues: list[Cue] = Field(default_factory=list)


class Settings(Model):
    provider: Literal['codex', 'openai', 'gemini'] = 'codex'
    model: str = ''
    language: str = 'Vietnamese'
    asr_model: str = 'small'
    asr_device: Literal['cpu', 'cuda'] = 'cpu'
    background: str = Field(default='#101826', pattern=r'^#[0-9a-fA-F]{6}$')
    background_mode: Literal['color', 'blur'] = 'blur'
    layout_preset: Literal['classic', 'reference'] = 'reference'
    fit: Literal['contain', 'cover'] = 'cover'
    crop_x: float = Field(default=50, ge=0, le=100)
    crop_y: float = Field(default=50, ge=0, le=100)
    title: str = Field(default='', max_length=220)
    title_size: int = Field(default=36, ge=24, le=90)
    subtitle_size: int = Field(default=48, ge=24, le=80)
    subtitle_color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    subtitle_position: Literal['below', 'inside'] = 'inside'
    subtitles: bool = True
    source_subtitle_blur: bool = False
    subtitle_highlight: bool = True
    subtitle_highlight_color: str = Field(default='#38bdf8', pattern=r'^#[0-9a-fA-F]{6}$')
    original_volume: float = Field(default=1, ge=0, le=2)
    duck_volume: float = Field(default=.15, ge=0, le=1)
    voice_volume: float = Field(default=1, ge=0, le=2)
    narration_mode: Literal['overlay', 'insert'] = 'overlay'
    hook_enabled: bool = False
    hook_start: float = Field(default=0, ge=0)
    hook_end: float = Field(default=5, gt=0)
    # None preserves historical timelines until the user selects an output mode.
    output_mode: Literal['single', 'parts'] | None = None
    summary_seconds: float = Field(default=180, ge=0, le=1800)
    part_count: int = Field(default=3, ge=1, le=100)
    opening_delay: float = Field(default=3, ge=0, le=15)
    part_seconds: float = Field(default=60, ge=10, le=1800)
    split_mode: Literal['exact', 'natural'] = 'natural'
    part_durations: list[float] = Field(default_factory=list, max_length=200)
    omnivoice_url: str = 'http://127.0.0.1:8001'
    voice_mode: Literal['design', 'clone'] = 'design'
    voice_reference: str = ''
    voice_reference_text: str = ''
    voice_reference_hash: str = ''
    voice_instruct: str = ''
    voice_speed: float = Field(default=1, ge=0.5, le=1.5)
    voice_gender: str = 'Auto'
    voice_steps: int = Field(default=32, ge=4, le=64)
    narration_style: Literal['highlights', 'storytelling'] = 'highlights'
    original_dialogue_ratio: float = Field(default=.15, ge=.1, le=.2)
    analysis_workflow: Literal['efficient', 'detailed'] = 'efficient'
    review_enabled: bool = True
    draft_rule: str = 'Kể diễn biến chính xác theo hình và lời thoại. Câu ngắn, cuốn hút, không bịa tình tiết. Xen lời dẫn ở khoảng nghỉ; giữ các câu thoại quan trọng. Mỗi câu gắn mốc video và bằng chứng.'
    review_rule: str = 'Sửa lỗi tên, tình tiết và logic; bỏ câu thừa và bình luận cá nhân. Kiểm tra mốc hình khớp từng ý. Giữ đúng cấu trúc JSON.'
    summary_rule: str = 'Tóm tắt ngắn, khách quan tới hết đoạn: nhân vật, quan hệ, sự kiện và tình huống hiện tại; giữ các chi tiết cũ còn liên quan.'

    @model_validator(mode='after')
    def validate_settings(self):
        if any(x < 1 or x > 1800 for x in self.part_durations):
            raise ValueError('Độ dài phần thủ công phải từ 1 đến 1800 giây.')
        return self


class ProjectEdit(Model):
    revision: int
    name: str = Field(min_length=1, max_length=180)
    settings: Settings
    narrations: list[Narration] = Field(default_factory=list, max_length=1000)
    transcript: list[Cue] = Field(default_factory=list, max_length=50000)


# All AI fields are required: compatible with strict Structured Outputs.
class SceneAnswer(Model):
    start: float
    end: float
    description: str
    characters: list[str]
    evidence: str
    confidence: float


class NarrationAnswer(Model):
    start: float
    text: str
    evidence: str


class HookAnswer(Model):
    start: float
    end: float
    title: str
    reason: str


class AnalysisAnswer(Model):
    scenes: list[SceneAnswer]
    narrations: list[NarrationAnswer]
    hooks: list[HookAnswer]
    summary: str
    requires_insert: bool


class TranslatedText(Model):
    id: str
    text: str


class TranslationAnswer(Model):
    items: list[TranslatedText]


class StorySelection(Model):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    part: int = Field(ge=1, le=100)
    section: Literal['opening', 'development', 'ending']
    reason: str = Field(min_length=1)
    priority: float = Field(ge=0, le=1)
    narration: str
    narration_offset: float = Field(ge=0)
    evidence: str = Field(min_length=1)


class StoryAnswer(Model):
    title: str = Field(min_length=1, max_length=220)
    synopsis: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    lesson: str = Field(min_length=1)
    hook: HookAnswer
    selections: list[StorySelection] = Field(min_length=3, max_length=500)
