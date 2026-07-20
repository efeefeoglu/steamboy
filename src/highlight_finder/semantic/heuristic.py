from ..models import CandidateAnalysis, SemanticScore


class HeuristicAnalyzer:
    def analyze(self, candidate: CandidateAnalysis) -> SemanticScore:
        m = candidate.metrics
        motion = m.get("motion", 0)
        variety = (m.get("visual_change", 0) + m.get("colorfulness", 0) + m.get("detail", 0)) / 3
        penalty = max(m.get("black_probability", 0), m.get("static_probability", 0))
        interest = min(1.0, 0.5 * motion + 0.5 * variety)
        suitable = max(
            0.0, min(1.0, 0.55 * interest + 0.25 * m.get("audio_activity", 0) + 0.2 * (1 - penalty))
        )
        return SemanticScore(
            gameplay_probability=max(0, 1 - penalty),
            menu_or_title_card_probability=penalty,
            visual_interest=interest,
            self_contained=0.5,
            action_completion=0.5,
            short_form_suitability=suitable,
            summary="Locally estimated from visual and audio signals.",
            selection_reason="Motion, visual variety, audio activity, and low static/black-frame estimates support this candidate.",
            rejection_reason="Static or black-frame estimates may reduce its rank.",
            estimated=True,
        )
