"""
Role-Scoped Feature Schema (MLB)
================================
Single source of truth for which feature families are LIVE per stat type.

A batter row has no use for the pitcher's own form columns, and a pitcher row has none for
the batter's: left in, they would sit at their defaults on every row, carry no signal and
distort imputation and scaling. Each stat type therefore keeps only the families that
describe it and its opponent (``opp_pitcher_*`` for batters, ``opp_team_*`` for pitchers).
The opponent families are the opposing starter's or lineup's own features under a prefix;
they are added where a row's features are assembled.

``drop_dead_features`` is meant to be applied wherever a row's features are assembled, so the
columns stay identical per stat type.

Prefix semantics (which side each family describes):
    pitcher_ / statcast_pitcher_ / pitchmix_ / saber_pitcher_
        -> the target player's own pitching form: pitcher stats only.
    batter_ / statcast_batter_ / battedball_ / saber_batter_ / volume_ /
    h2h_ / bvp_
        -> the target player's own batting form + opportunity + BvP:
           batter stats only.
    opp_pitcher_   -> the OPPOSING STARTER (batter stats only).
    opp_team_      -> the OPPOSING LINEUP (pitcher stats only).
Shared families (temporal, game context, is_home) carry no role prefix
and are never dropped.
"""

from typing import Dict, Iterable, List

PITCHER_STATS = ("STRIKEOUTS",)
BATTER_STATS = ("HITS", "TOTAL_BASES")

# Families that are pure defaults (dead) for BATTER-stat rows.
_DEAD_FOR_BATTER = (
    "pitcher_",  # own-role pitching form (not opp_pitcher_ - see below)
    "statcast_pitcher_",
    "pitchmix_",
    "saber_pitcher_",
    "opp_team_",  # opposing-lineup family belongs to pitcher stats
)

# Families that are pure defaults (dead) for PITCHER-stat rows.
_DEAD_FOR_PITCHER = (
    "batter_",  # own-role batting form (also batter_vs_hand_advantage)
    "statcast_batter_",
    "battedball_",
    "saber_batter_",
    "volume_",  # lineup slot / expected PA / opposing bullpen
    "h2h_",  # real BvP
    "bvp_",  # platoon BvP proxy
    "opp_pitcher_",  # opposing-starter family belongs to batter stats
)


def is_dead_feature(name: str, stat_type: str) -> bool:
    """True when ``name`` belongs to a family that is dead for ``stat_type``."""
    stat_u = (stat_type or "").upper()
    if stat_u in PITCHER_STATS:
        dead = _DEAD_FOR_PITCHER
    elif stat_u in BATTER_STATS:
        dead = _DEAD_FOR_BATTER
    else:
        return False
    # ``pitcher_`` must not swallow ``opp_pitcher_`` (dead-prefix sets keep the
    # opp_* families explicit, and no dead prefix is a prefix of a live one).
    return name.startswith(dead)


def drop_dead_features(features: Dict[str, float], stat_type: str) -> Dict[str, float]:
    """Return ``features`` without the families that are dead for ``stat_type``."""
    return {k: v for k, v in features.items() if not is_dead_feature(k, stat_type)}


def live_feature_names(names: Iterable[str], stat_type: str) -> List[str]:
    """Filter an ordered name list down to the live families for ``stat_type``."""
    return [n for n in names if not is_dead_feature(n, stat_type)]
