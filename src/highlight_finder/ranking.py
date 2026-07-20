from .config import RankingWeights
from .models import CandidateAnalysis


def rank_candidate(candidate: CandidateAnalysis, weights: RankingWeights) -> float:
    assert candidate.semantic
    s, m = candidate.semantic, candidate.metrics
    components = {
        "visual_interest": s.visual_interest,
        "self_contained": s.self_contained,
        "action_completion": s.action_completion,
        "short_form_suitability": s.short_form_suitability,
        "motion": m.get("motion", 0),
        "audio_activity": m.get("audio_activity", 0),
        "visual_variety": (m.get("visual_change", 0) + m.get("colorfulness", 0)) / 2,
        "menu_penalty": s.menu_or_title_card_probability,
        "black_static_penalty": max(m.get("black_probability", 0), m.get("static_probability", 0)),
    }
    positive = sum(components[k] * getattr(weights, k) for k in components if "penalty" not in k)
    penalty = sum(components[k] * getattr(weights, k) for k in components if "penalty" in k)
    candidate.ranking_components = components
    candidate.total_score = max(0.0, min(1.0, positive - penalty))
    return candidate.total_score


def overlap_ratio(a: CandidateAnalysis, b: CandidateAnalysis) -> float:
    intersection = max(0, min(a.end, b.end) - max(a.start, b.start))
    return intersection / min(a.duration, b.duration) if min(a.duration, b.duration) else 0


def deduplicate(
    candidates: list[CandidateAnalysis], threshold: float, limit: int
) -> list[CandidateAnalysis]:
    chosen = []
    for candidate in sorted(candidates, key=lambda c: c.total_score, reverse=True):
        if all(overlap_ratio(candidate, existing) < threshold for existing in chosen):
            chosen.append(candidate)
        if len(chosen) == limit:
            break
    return chosen
