"""MLB team abbreviations: the canonical set, and the spellings other feeds use for them."""

# Non-standard / source-specific -> canonical MLB abbreviation.
TEAM_NORMALIZATION_MAP = {
    # Common alternate codes seen across other feeds
    "CHW": "CWS",  # White Sox
    "CHC": "CHC",  # Cubs (canonical)
    "KC": "KCR",  # Royals
    "KAN": "KCR",
    "SD": "SDP",  # Padres
    "SF": "SFG",  # Giants
    "SFO": "SFG",
    "TB": "TBR",  # Rays
    "TAM": "TBR",
    "WSH": "WSN",  # Nationals
    "WAS": "WSN",
    "WSP": "WSN",
    "ARI": "ARI",
    "AZ": "ARI",  # Diamondbacks alt
    "LA": "LAD",  # Dodgers (ambiguous; default Dodgers)
    "NY": "NYY",  # ambiguous; default Yankees
    "OAK": "ATH",  # Athletics rebrand
    "ATH": "ATH",
}

# Canonical 30-team set for validation.
VALID_TEAMS = {
    "ARI",
    "ATL",
    "BAL",
    "BOS",
    "CHC",
    "CWS",
    "CIN",
    "CLE",
    "COL",
    "DET",
    "HOU",
    "KCR",
    "LAA",
    "LAD",
    "MIA",
    "MIL",
    "MIN",
    "NYM",
    "NYY",
    "ATH",
    "PHI",
    "PIT",
    "SDP",
    "SEA",
    "SFG",
    "STL",
    "TBR",
    "TEX",
    "TOR",
    "WSN",
}


def normalize_team_abbrev(abbrev: str) -> str:
    """
    Normalize an MLB team abbreviation to its canonical 3-letter code.

    Examples:
        >>> normalize_team_abbrev("CHW")
        'CWS'
        >>> normalize_team_abbrev("SD")
        'SDP'
        >>> normalize_team_abbrev("NYY")
        'NYY'
    """
    if not abbrev:
        return ""
    a = abbrev.strip().upper()
    return TEAM_NORMALIZATION_MAP.get(a, a)


def validate_team_abbrev(abbrev: str) -> bool:
    """Return True if ``abbrev`` (after normalization) is a known MLB team."""
    return normalize_team_abbrev(abbrev) in VALID_TEAMS
