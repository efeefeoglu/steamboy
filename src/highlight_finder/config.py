from pydantic import BaseModel, Field, model_validator


class RankingWeights(BaseModel):
    visual_interest: float = 0.17
    self_contained: float = 0.15
    action_completion: float = 0.13
    short_form_suitability: float = 0.17
    motion: float = 0.10
    audio_activity: float = 0.07
    visual_variety: float = 0.09
    menu_penalty: float = 0.06
    black_static_penalty: float = 0.06


class AnalysisConfig(BaseModel):
    min_duration: float = Field(8, gt=0)
    max_duration: float = Field(30, gt=0)
    max_results: int = Field(5, ge=1)
    sample_fps: float = Field(2, gt=0, le=10)
    semantic_limit: int = Field(20, ge=1)
    contact_sheet_frames: int = Field(8, ge=6, le=10)
    overlap_threshold: float = Field(0.65, ge=0, le=1)
    weights: RankingWeights = RankingWeights()

    @model_validator(mode="after")
    def durations_are_ordered(self):
        if self.max_duration < self.min_duration:
            raise ValueError("max_duration must be at least min_duration")
        return self
