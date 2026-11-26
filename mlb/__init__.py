"""
MLB player statistics platform
==============================
Data loading, feature engineering and stat-projection models for MLB players. Mirrors the NBA
package (see ``nba/``), adapted for baseball's two player types: pitchers and batters.

Subpackages:
    scripts: Data loaders (MLB Stats API, Statcast) and backfills
    features: Feature extraction, the feature schema and the feature store
    models: The stat projection and experiment tracking
    core: Exception types
    config: Configuration and database routing
    utils: Utility functions
"""

__version__ = "0.1.0"
__author__ = "untitled114"
