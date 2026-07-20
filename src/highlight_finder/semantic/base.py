from typing import Protocol

from ..models import CandidateAnalysis, SemanticScore


class SemanticAnalyzer(Protocol):
    def analyze(self, candidate: CandidateAnalysis) -> SemanticScore: ...
