from pathlib import Path

import pytest

from backend.voice_repair import (
    DurationMismatchError,
    RepairBudget,
    duration_is_acceptable,
    repair_prompt,
    repair_text,
    speech_units,
)
from backend import media, providers, store


@pytest.fixture
def synthesis_project(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DB", tmp_path / "test.sqlite3")
    store.init()
    project = store.create("Targeted repair", {"kind": "upload", "file": "source.mp4"})
    project["settings"].update(
        language="English", tts_provider="omnivoice", voice_mode="clone", production_workflow="legacy",
        voice_reference="reference.wav", voice_reference_text="Reference words.",
    )
    store.asset(project["id"], "reference.wav").write_bytes(b"reference")
    project["narrations"] = [
        {
            "id": "n0", "start": 1, "text": "Already rendered.", "enabled": True,
            "evidence": "frame 1", "segment_id": "s0", "target_duration": 4,
            "audio": "", "audio_hash": "", "duration": 4, "cues": [],
            "caption_version": 4,
        },
        {
            "id": "n1", "start": 8, "text": "Too long sentence", "enabled": True,
            "evidence": "frame 2", "segment_id": "s1", "target_duration": 4,
            "audio": "", "audio_hash": "", "duration": 0, "cues": [],
            "caption_version": 0,
        },
    ]
    first = project["narrations"][0]
    first_hash = providers.voice_hash(first, project["settings"])
    first_path = store.project_dir(project["id"]) / "voices" / f"{first_hash}.wav"
    first_path.parent.mkdir(exist_ok=True)
    first_path.write_bytes(b"cached")
    first.update(audio=f"voices/{first_path.name}", audio_hash=first_hash)
    return project


def test_duration_error_is_distinct_from_transport_errors():
    error = DurationMismatchError("VieNeu", 12.0, 6.0)
    assert error.measured == 12
    assert error.target == 6
    assert "không khớp" in str(error)
    assert isinstance(RuntimeError("network"), RuntimeError)


def test_final_duration_check_has_absolute_floor_and_relative_tolerance():
    assert duration_is_acceptable(10.02, 10)
    assert not duration_is_acceptable(10.4, 10)
    assert duration_is_acceptable(0.94, 1)
    assert not duration_is_acceptable(1.2, 1)


def test_repair_prompt_contains_measured_ratio_and_evidence():
    prompt = repair_prompt("The dog runs.", "frame 17", "English", 8, 4)
    assert "8.000" in prompt and "4.000" in prompt
    assert "frame 17" in prompt
    assert "expand" in prompt


def test_repair_text_validates_single_changed_budgeted_answer():
    calls = []

    def ask_ai(prompt, images, settings, folder, check, model):
        calls.append(prompt)
        return {"items": [{"id": "narration", "text": "The dog runs toward the open field."}]}

    result = repair_text(
        "The dog runs.", "frame 17", "English", 8, 4, {"language": "English"},
        None, lambda: None, ask_ai,
    )
    assert result.startswith("The dog runs toward")
    assert calls and "narration" in calls[0]


def test_repair_text_rejects_wrong_shape_or_unbounded_answer():
    def ask_ai(*args):
        return {"items": [{"id": "other", "text": "A long answer."}]}

    with pytest.raises(ValueError, match="đúng JSON"):
        repair_text("Short.", "frame", "English", 8, 4, {}, None, lambda: None, ask_ai)


def test_repair_text_does_not_accept_unchanged_text():
    def ask_ai(*args):
        return {"items": [{"id": "narration", "text": "Short."}]}

    with pytest.raises(ValueError, match="không hợp lệ sau giới hạn retry"):
        repair_text("Short.", "frame", "English", 8, 4, {}, None, lambda: None, ask_ai)


def test_repair_text_propagates_network_failures_without_reclassifying_them():
    def ask_ai(*args):
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        repair_text("Short.", "frame", "English", 8, 4, {}, None, lambda: None, ask_ai)


def test_repair_text_honors_cancellation_before_calling_ai():
    def cancelled():
        raise RuntimeError("cancelled")

    def unexpected(*args):
        raise AssertionError("cancelled jobs must not call the AI")

    with pytest.raises(RuntimeError, match="cancelled"):
        repair_text("Short.", "frame", "English", 8, 4, {}, None, cancelled, unexpected)


def test_job_and_per_narration_repair_limits_are_bounded():
    budget = RepairBudget(max_per_narration=2, max_total=3)
    assert budget.consume(0)
    assert budget.consume(1)
    assert not budget.consume(2)
    assert budget.total == 2
    assert speech_units("One two three") == 3


def test_synthesize_skips_completed_cache_and_repairs_only_failed_narration(
    synthesis_project, monkeypatch
):
    project = synthesis_project
    monkeypatch.setattr(
        "backend.reference_voice.ensure_transcript", lambda *args, **kwargs: None
    )
    generated = []

    def fake_generate(client, params, endpoint, destination, report, check,
                      progress, label, target_duration=0, **kwargs):
        text = params["text"]
        generated.append(text)
        if text == "Too long sentence":
            raise DurationMismatchError("OmniVoice", 8, target_duration)
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(b"fixed")

    monkeypatch.setattr(providers, "generate_voice_audio", fake_generate)
    monkeypatch.setattr(providers, "probe", lambda path: {"duration": 4})
    aligned = []
    monkeypatch.setattr(
        providers, "transcribe",
        lambda path, settings, check, expected_text=None: aligned.append(expected_text)
        or [{"id": "c0", "start": 0, "end": 4, "text": expected_text}],
    )
    monkeypatch.setattr(
        providers, "ask_ai",
        lambda *args: {"items": [{"id": "narration", "text": "Fixed phrase"}]},
    )
    result = providers.synthesize(project, lambda *args: None, lambda: None)
    assert generated == ["Too long sentence", "Fixed phrase"]
    assert aligned == ["Fixed phrase"]
    assert result["narrations"][0]["text"] == "Already rendered."
    assert result["narrations"][0]["caption_version"] == 4
    assert result["narrations"][1]["text"] == "Fixed phrase"
    assert result["narrations"][1]["caption_version"] == 4


def test_invalid_repair_answers_are_bounded_and_use_fresh_prompts(
    synthesis_project, monkeypatch
):
    project = synthesis_project
    monkeypatch.setattr(
        "backend.reference_voice.ensure_transcript", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(providers, "probe", lambda path: {"duration": 4})
    monkeypatch.setattr(
        providers, "generate_voice_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DurationMismatchError("OmniVoice", 8, kwargs.get("target_duration", 4))
        ),
    )
    prompts = []

    def invalid_ai(prompt, *args):
        prompts.append(prompt)
        return {"items": [{"id": "wrong", "text": "Invalid"}]}

    monkeypatch.setattr(providers, "ask_ai", invalid_ai)
    with pytest.raises(ValueError, match="giới hạn retry"):
        providers.synthesize(project, lambda *args: None, lambda: None)
    assert len(prompts) == 2
    assert "REPAIR GENERATION 1.1" in prompts[0]
    assert "REPAIR GENERATION 1.2" in prompts[1]


def test_synthesize_does_not_convert_network_or_cancel_to_duration_repair(
    synthesis_project, monkeypatch
):
    project = synthesis_project
    monkeypatch.setattr(
        "backend.reference_voice.ensure_transcript", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(providers, "probe", lambda path: {"duration": 4})
    monkeypatch.setattr(
        providers, "generate_voice_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(
        providers, "ask_ai", lambda *args: pytest.fail("network error must not call AI")
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        providers.synthesize(project, lambda *args: None, lambda: None)
