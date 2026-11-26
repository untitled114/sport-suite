#!/usr/bin/env python3
"""
Drift Detection CLI Tool
========================
Manual drift checking and reference distribution generation.

Without --training-file the command expects a training dataset and a saved model's feature
list in their usual folders; with it, any training CSV can be used.

Usage:
    # Generate reference distributions from a training CSV
    python -m nba.core.cli_drift_check --stat-type POINTS --generate-reference \
        --training-file path/to/training.csv

    # Show the reference loaded for one stat type
    python -m nba.core.cli_drift_check --stat-type POINTS --show-reference

    # Get drift service status
    python -m nba.core.cli_drift_check --status
"""

import argparse
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
REFERENCE_DIR = PROJECT_ROOT / "nba" / "models" / "reference_distributions"
TRAINING_DIR = PROJECT_ROOT / "nba" / "features" / "datasets"


def generate_reference(
    stat_type: str, model_version: str = "base", training_path: Optional[str] = None
) -> int:
    """Generate reference distributions from a training CSV.

    With no path the conventional dataset for the stat type is read, and the reference is limited
    to the features of the saved model. With a path, any training CSV can be used; if no saved
    feature list is found for the model version, every numeric column of the CSV is recorded.
    """
    from nba.core.reference_distributions import build_from_training_dataset, load_model_features

    stat_upper = stat_type.upper()
    stat_lower = stat_type.lower()

    if training_path:
        training_file = Path(training_path)
    else:
        training_file = TRAINING_DIR / f"training_{stat_upper}_2023_2025.csv"

    if not training_file.exists():
        logger.error(f"Training data not found: {training_file} (pass --training-file)")
        return 1

    # Load model features to ensure reference matches deployed model
    try:
        model_features = load_model_features(stat_upper, model_version)
        logger.info(f"Using {len(model_features)} features from {model_version} model")
    except FileNotFoundError as e:
        if not training_path:
            logger.error(f"Model features not found: {e}")
            return 1
        model_features = None
        logger.info("No saved feature list for this model: using every numeric column")

    # Create output directory
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REFERENCE_DIR / f"{stat_lower}_reference.json"

    logger.info(f"Building reference distributions for {stat_upper}...")
    reference = build_from_training_dataset(
        str(training_file),
        stat_upper,
        str(output_path),
        model_features=model_features,
    )

    print(f"\nReference Distributions Generated:")
    print(f"  Stat type: {reference.stat_type}")
    print(f"  Model: {model_version.upper()}")
    print(f"  Features: {len(reference.features)}")
    print(f"  Training Samples: {reference.training_samples}")
    print(f"  Output: {output_path}")

    return 0


def show_reference(stat_type: str) -> int:
    """Report whether a stat type's reference is loaded, and its size."""
    from nba.core.drift_service import DriftService

    service = DriftService(stat_type)
    status = service.get_status()

    if status["status"] != "ready":
        logger.error(f"Drift service not ready: {status['status']}")
        logger.info("Run with --generate-reference first")
        return 1

    print(f"\nDrift Service Status:")
    print(f"  Stat type: {status['stat_type']}")
    print(f"  Features: {status['features']}")
    print(f"  Reference Created: {status['reference_created']}")
    print(f"  Training Samples: {status['training_samples']}")

    return 0


def show_status() -> int:
    """Show drift service status for all stat types."""
    from nba.core.drift_service import DriftService

    print("\nDrift Detection Status:")
    print("=" * 60)

    for stat_type in ["POINTS", "REBOUNDS", "ASSISTS", "THREES"]:
        service = DriftService(stat_type)
        status = service.get_status()

        status_icon = "OK" if status["status"] == "ready" else "FAILED"
        print(f"\n{status_icon} {stat_type}")
        print(f"   Status: {status['status']}")

        if status["status"] == "ready":
            print(f"   Features: {status['features']}")
            print(f"   Reference: {status['reference_created']}")

    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Drift detection CLI tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--stat-type",
        choices=["POINTS", "REBOUNDS", "ASSISTS", "THREES"],
        help="Stat type to analyze",
    )

    parser.add_argument(
        "--generate-reference",
        action="store_true",
        help="Generate reference distributions from training data",
    )

    parser.add_argument(
        "--show-reference",
        action="store_true",
        help="Show the reference loaded for --stat-type",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Show drift service status",
    )

    parser.add_argument(
        "--training-file",
        help="Training CSV to build the reference from (default: the stat type's dataset)",
    )

    parser.add_argument(
        "--model-version",
        choices=["base", "extended"],
        default="base",
        help="Model version to use for feature list (default: base)",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    if args.status:
        return show_status()

    if args.generate_reference:
        if not args.stat_type:
            logger.error("--stat-type required with --generate-reference")
            return 1
        return generate_reference(args.stat_type, args.model_version, args.training_file)

    if args.show_reference:
        if not args.stat_type:
            logger.error("--stat-type required with --show-reference")
            return 1
        return show_reference(args.stat_type)

    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
