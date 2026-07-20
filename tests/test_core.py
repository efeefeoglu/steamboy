import json

import pytest

from highlight_finder.candidates import generate_candidates
from highlight_finder.config import AnalysisConfig, RankingWeights
from highlight_finder.models import CandidateAnalysis, MediaInfo, Scene, SemanticScore
from highlight_finder.ranking import deduplicate, rank_candidate
from highlight_finder.visual import robust_normalize


def semantic(**values):
    defaults = dict(
        gameplay_probability=0.8,
        menu_or_title_card_probability=0.1,
        visual_interest=0.8,
        self_contained=0.7,
        action_completion=0.6,
        short_form_suitability=0.9,
        summary="x",
        selection_reason="y",
    )
    return SemanticScore(**(defaults | values))


def test_combines_scenes_and_enforces_duration():
    scenes = [Scene(number=i + 1, start=i * 5, end=(i + 1) * 5) for i in range(5)]
    result = generate_candidates(scenes, [], [], 8, 12)
    assert result
    assert all(8 <= c.duration <= 12 for c in result)
    assert result[0].scene_numbers == [1, 2]


def test_one_short_scene_gets_best_effort_candidate():
    result = generate_candidates([Scene(number=1, start=0, end=3)], [], [], 8, 30)
    assert len(result) == 1 and result[0].duration == 3


def test_robust_normalization_and_constant_input():
    normalized = robust_normalize([0, 1, 2, 100])
    assert min(normalized) == 0 and max(normalized) == 1
    assert robust_normalize([4, 4]) == [0, 0]


def test_ranking_and_penalties():
    good = CandidateAnalysis(
        id=1,
        start=0,
        end=10,
        scene_numbers=[1],
        metrics={"motion": 0.8, "audio_activity": 0.5, "visual_change": 0.7, "colorfulness": 0.6},
        semantic=semantic(),
    )
    bad = CandidateAnalysis(
        id=2,
        start=12,
        end=22,
        scene_numbers=[2],
        metrics={"black_probability": 1, "static_probability": 1},
        semantic=semantic(menu_or_title_card_probability=1),
    )
    assert rank_candidate(good, RankingWeights()) > rank_candidate(bad, RankingWeights())
    assert good.ranking_components["motion"] == 0.8


def test_overlap_deduplication_keeps_higher_score():
    high = CandidateAnalysis(id=1, start=0, end=10, scene_numbers=[1], total_score=0.9)
    low = CandidateAnalysis(id=2, start=2, end=11, scene_numbers=[1], total_score=0.4)
    separate = CandidateAnalysis(id=3, start=20, end=30, scene_numbers=[2], total_score=0.5)
    assert [c.id for c in deduplicate([low, separate, high], 0.65, 5)] == [1, 3]


def test_missing_audio_and_json_serialization():
    from highlight_finder.audio import analyze_audio

    assert analyze_audio(__file__, False, 2) == []
    media = MediaInfo(
        path="x.mp4",
        duration=1,
        width=1,
        height=1,
        frame_rate=30,
        has_audio=False,
        video_codec="h264",
    )
    assert json.loads(media.model_dump_json())["has_audio"] is False


def test_config_rejects_inverted_duration():
    with pytest.raises(ValueError):
        AnalysisConfig(min_duration=10, max_duration=5)
