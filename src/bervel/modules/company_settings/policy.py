"""User-facing defaults shared by company settings and the current radar score."""

from typing import TypedDict


class EffectiveScoringPolicy(TypedDict):
    version: str
    target_margin: str
    comfortable_utilization: str
    minimum_production_factor: str
    high_score: int
    medium_score: int


DEFAULT_SCORING_POLICY: EffectiveScoringPolicy = {
    'version': 'score-v2',
    'target_margin': '0.30',
    'comfortable_utilization': '0.70',
    'minimum_production_factor': '0.60',
    'high_score': 80,
    'medium_score': 50,
}


def effective_scoring_policy(overrides: dict | None) -> EffectiveScoringPolicy:
    overrides = overrides or {}
    return EffectiveScoringPolicy(
        version=overrides.get('version', DEFAULT_SCORING_POLICY['version']),
        target_margin=overrides.get('target_margin', DEFAULT_SCORING_POLICY['target_margin']),
        comfortable_utilization=overrides.get('comfortable_utilization', DEFAULT_SCORING_POLICY['comfortable_utilization']),
        minimum_production_factor=overrides.get('minimum_production_factor', DEFAULT_SCORING_POLICY['minimum_production_factor']),
        high_score=overrides.get('high_score', DEFAULT_SCORING_POLICY['high_score']),
        medium_score=overrides.get('medium_score', DEFAULT_SCORING_POLICY['medium_score']),
    )
