from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class MediaInfo(BaseModel):
    path: Path
    duration: float
    width: int
    height: int
    frame_rate: float
    has_audio: bool
    video_codec: str
    audio_codec: str | None = None


class Scene(BaseModel):
    number: int
    start: float
    end: float
    transition: str = "cut"

    @property
    def duration(self) -> float:
        return self.end - self.start


class MetricSample(BaseModel):
    time: float
    motion: float = 0
    optical_flow: float = 0
    brightness: float = 0
    black_probability: float = 0
    static_probability: float = 0
    detail: float = 0
    colorfulness: float = 0
    visual_change: float = 0
    audio_energy: float = 0
    loudness: float = 0
    energy_change: float = 0
    onset_peak: float = 0
    silence_probability: float = 1


class SemanticScore(BaseModel):
    gameplay_probability: float = Field(ge=0, le=1)
    menu_or_title_card_probability: float = Field(ge=0, le=1)
    visual_interest: float = Field(ge=0, le=1)
    self_contained: float = Field(ge=0, le=1)
    action_completion: float = Field(ge=0, le=1)
    short_form_suitability: float = Field(ge=0, le=1)
    summary: str
    selection_reason: str
    rejection_reason: str = ""
    estimated: bool = True


class CandidateAnalysis(BaseModel):
    id: int
    start: float
    end: float
    scene_numbers: list[int]
    metrics: dict[str, float] = Field(default_factory=dict)
    semantic: SemanticScore | None = None
    ranking_components: dict[str, float] = Field(default_factory=dict)
    total_score: float = 0
    contact_sheet: str | None = None
    clip: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


class AnalysisResult(BaseModel):
    input: MediaInfo
    configuration: dict[str, Any]
    scenes: list[Scene]
    metrics: list[MetricSample]
    candidates: list[CandidateAnalysis]
    selected_candidate_ids: list[int]
    errors: list[str] = Field(default_factory=list)
