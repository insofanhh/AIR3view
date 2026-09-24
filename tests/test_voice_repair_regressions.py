"""Regressions for targeted narration repair safety and measured fitting."""
import pytest

from backend import providers, store
from backend.voice_repair import DurationMismatchError, repair_prompt, repair_text, speech_units


LONG_TEXT = " ".join(f"detail{i}" for i in range(68))
VALID_TEXT = " ".join("clear" for _ in range(30))
EIGHTEEN_WORDS = (
    "The officer follows the dog across the open field while the camera "
    "records each careful step toward safety."
)
VIETNAMESE_TEXT = (
    "Những người này đã không được các nhân viên cho phép vào trong khu vực "
    "vì họ không có giấy tờ."
)


def call_repair(answers, *, text=LONG_TEXT, max_attempts=3):
    prompts = []
    iterator = iter(answers)

    def ask_ai(prompt, *args):
        prompts.append(prompt)
        return {"items": [{"id": "narration", "text": next(iterator)}]}

    result = repair_text(
        text, "frame 22 and transcript cue", "English", 10, 20,
        {"language": "English"}, None, lambda: None, ask_ai,
        max_attempts=max_attempts,
    )
    return result, prompts


def test_first_short_answer_then_valid_answer_succeeds_without_third_call():
    result, prompts = call_repair(["Too short.", VALID_TEXT, VIETNAMESE_TEXT])
    assert result == VALID_TEXT
    assert len(prompts) == 2
    assert "REPAIR GENERATION 1.1" in prompts[0]
    assert "REPAIR GENERATION 1.2" in prompts[1]


def test_eighteen_word_candidate_is_allowed_through_for_measured_tts_fit():
    assert speech_units(EIGHTEEN_WORDS) == 18
    prompt = repair_prompt(LONG_TEXT, "frame 22", "English", 10, 20)
    assert "between 24 and 44" in prompt
    result, _ = call_repair([EIGHTEEN_WORDS], max_attempts=1)
    assert result == EIGHTEEN_WORDS


@pytest.mark.parametrize("candidate", ["", LONG_TEXT, VIETNAMESE_TEXT])
def test_empty_unchanged_and_wrong_language_candidates_are_never_fallbacks(candidate):
    with pytest.raises(ValueError, match="không hợp lệ|không thể"):
        call_repair([candidate], max_attempts=1)


def test_measured_candidate_runs_tts_while_completed_audio_stays_untouched(
    tmp_path, monkeypatch
):
    import gradio_client

    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DB", tmp_path / "test.sqlite3")
    store.init()
    project = store.create("Regression", {"kind": "upload", "file": "source.mp4"})
    project["settings"].update(
        language="English", tts_provider="vieneu", voice_mode="design", production_workflow="legacy",
        vieneu_voice="Regression voice",
    )
    store.asset(project["id"], "reference.wav").write_bytes(b"reference")
    project["narrations"] = [
        {
            "id": "done", "start": 0, "text": "Completed narration.",
            "enabled": True, "evidence": "frame 1", "segment_id": "s0",
            "target_duration": 10, "audio": "", "audio_hash": "",
            "duration": 10, "caption_version": 4,
            "cues": [{"id": "c0", "start": 0, "end": 10,
                      "text": "Completed narration.", "speaker": "ai"}],
        },
        {
            "id": "repair", "start": 10, "text": LONG_TEXT,
            "enabled": True, "evidence": "frame 22", "segment_id": "s1",
            "target_duration": 10, "audio": "", "audio_hash": "",
            "duration": 0, "caption_version": 0, "cues": [],
        },
    ]
    completed = project["narrations"][0]
    completed_hash = providers.voice_hash(completed, project["settings"])
    completed_path = store.project_dir(project["id"]) / "voices" / f"{completed_hash}.wav"
    completed_path.parent.mkdir(exist_ok=True)
    completed_path.write_bytes(b"completed")
    completed.update(audio=f"voices/{completed_path.name}", audio_hash=completed_hash)

    monkeypatch.setattr("backend.vieneu.ensure_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.vieneu.parameters", lambda client, settings, text, *args: {"text": text})
    monkeypatch.setattr("backend.vieneu.synthesis_endpoint", lambda settings: "/wrapper")
    monkeypatch.setattr("backend.narration_groups.repair_existing", lambda project, report, check: project)
    monkeypatch.setattr(gradio_client, "Client", lambda *args, **kwargs: object())
    monkeypatch.setattr(providers, "probe", lambda path: {"duration": 10})

    generated = []

    def generate(client, params, endpoint, destination, report, check,
                 progress, label, target_duration=0, **kwargs):
        text = params["text"]
        generated.append(text)
        if text == LONG_TEXT:
            raise DurationMismatchError("OmniVoice", 20, target_duration)
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(b"fitted")

    monkeypatch.setattr(providers, "generate_voice_audio", generate)
    prompts = []

    def ask_ai(prompt, *args):
        prompts.append(prompt)
        return {"items": [{"id": "narration", "text": EIGHTEEN_WORDS}]}

    monkeypatch.setattr(providers, "ask_ai", ask_ai)
    aligned = []
    monkeypatch.setattr(
        providers, "transcribe",
        lambda path, settings, check, expected_text=None: aligned.append(expected_text)
        or [{"id": "c0", "start": 0, "end": 10,
             "text": expected_text, "speaker": "ai"}],
    )

    result = providers.synthesize(project, lambda *args: None, lambda: None)

    assert generated == [LONG_TEXT, EIGHTEEN_WORDS]
    assert aligned == [EIGHTEEN_WORDS]
    assert len(prompts) == 1 and "between 24 and 44" in prompts[0]
    assert completed_path.read_bytes() == b"completed"
    assert result["narrations"][0]["caption_version"] == 4
    assert result["narrations"][1]["text"] == EIGHTEEN_WORDS
    assert store.read(project["id"])["narrations"][1]["text"] == EIGHTEEN_WORDS
