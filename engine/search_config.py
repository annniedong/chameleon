"""Bounded offline search settings, read per call so tests can override them."""
from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class SearchConfig:
    interpretations: int = 4
    ambiguity_margin: float = 0.30
    candidates: int = 40
    refine_top: int = 3
    prior_weight: float = 0.08
    role_weight: float = 0.10
    relation_weight: float = 0.15

    @classmethod
    def from_env(cls):
        defaults = cls()
        limits = {
            "interpretations": (1, 8), "ambiguity_margin": (0, 1),
            "candidates": (1, 128), "refine_top": (0, 8),
            "prior_weight": (0, 1), "role_weight": (0, 1),
            "relation_weight": (0, 1),
        }
        values = {}
        for name, (lo, hi) in limits.items():
            default = getattr(defaults, name)
            try:
                value = float(os.environ.get("CHAMELEON_" + name.upper(), default))
                if not math.isfinite(value):
                    value = default
            except ValueError:
                value = default
            value = max(lo, min(hi, value))
            values[name] = int(value) if isinstance(default, int) else value
        return cls(**values)
