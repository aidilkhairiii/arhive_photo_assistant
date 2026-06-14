"""
Main Orchestrator - Archive Photo Assistant
==========================================

This is the central orchestrator that runs the entire pipeline:
1. Module 1: Preprocessing (preprocessing.py)
2. Module 2: Quality Assessment (quality_assestment.py)
3. Module 3: Fading Analysis (fading_analysis.py)
4. Module 4: Report Generation & Dashboard (report_generator.py)

It chains all the modules together and processes all images in batch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, List

# Import all modules
from preprocessing import process_dataset as preprocess_dataset
from quality_assestment import process_metadata_folder as assess_quality_folder
from fading_analysis import process_metadata_folder as analyze_fading_folder
from report_generator import process_metadata_folder as generate_reports_folder


def run_full_pipeline(
    input_path: str,
    output_dir: str,
    meta_dir: str,
    keep_aspect: bool = True,
    save_normalized: bool = False,
) -> List[dict[str, Any]]:
    """
    Run the complete pipeline on all images:
    1. Preprocess all raw images
    2. Assess quality for all processed images
    3. Analyze fading for all processed images
    4. Generate report cards + restoration-priority dashboard
    """
    print("=" * 70)
    print("ARCHIVE PHOTO ASSISTANT - FULL PIPELINE")
    print("=" * 70)

    # Module 1: Preprocessing
    print("\n[1/4] Running Module 1: Image Preprocessing...")
    preprocessed_metadata = preprocess_dataset(
        input_path=input_path,
        output_dir=output_dir,
        meta_dir=meta_dir,
        keep_aspect=keep_aspect,
        save_normalized=save_normalized,
    )

    # Module 2: Quality Assessment
    print("\n[2/4] Running Module 2: Quality Assessment...")
    quality_metadata = assess_quality_folder(
        metadata_dir=meta_dir,
        write_back=True,
    )

    # Module 3: Fading Analysis
    print("\n[3/4] Running Module 3: Fading Analysis...")
    analyze_fading_folder(
        metadata_dir=meta_dir,
        write_back=True,
    )

    # Module 4: Report Generation & Dashboard
    print("\n[4/4] Running Module 4: Report Generation...")
    final_metadata = generate_reports_folder(
        metadata_dir=meta_dir,
        write_back=True,
    )

    # Summary
    ok_count = sum(1 for m in final_metadata if m.get("status") == "ok")
    print("\n" + "=" * 70)
    print(f"PIPELINE COMPLETE! Successfully processed {ok_count}/{len(final_metadata)} images")
    print("=" * 70)
    
    return final_metadata


def run_existing_metadata(
    meta_dir: str,
) -> List[dict[str, Any]]:
    """
    Run only the analysis modules (2-4) on existing metadata JSON files.
    Use this if preprocessing is already done.
    """
    print("=" * 70)
    print("RUNNING ANALYSIS ON EXISTING METADATA")
    print("=" * 70)

    # Module 2: Quality Assessment
    print("\n[1/3] Running Module 2: Quality Assessment...")
    quality_metadata = assess_quality_folder(
        metadata_dir=meta_dir,
        write_back=True,
    )

    # Module 3: Fading Analysis
    print("\n[2/3] Running Module 3: Fading Analysis...")
    analyze_fading_folder(
        metadata_dir=meta_dir,
        write_back=True,
    )

    # Module 4: Report Generation & Dashboard
    print("\n[3/3] Running Module 4: Report Generation...")
    final_metadata = generate_reports_folder(
        metadata_dir=meta_dir,
        write_back=True,
    )

    # Summary
    ok_count = sum(1 for m in final_metadata if m.get("status") == "ok")
    print("\n" + "=" * 70)
    print(f"ANALYSIS COMPLETE! Processed {ok_count}/{len(final_metadata)} images")
    print("=" * 70)
    
    return final_metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Main orchestrator for Archive Photo Assistant - runs the complete pipeline."
    )
    parser.add_argument(
        "--input", default="data/raw",
        help="Input folder with raw images (default: data/raw)",
    )
    parser.add_argument(
        "--output", default="data/processed",
        help="Output folder for processed images (default: data/processed)",
    )
    parser.add_argument(
        "--meta", default="outputs",
        help="Metadata folder for JSON files (default: outputs)",
    )
    parser.add_argument(
        "--stretch", action="store_true",
        help="Stretch images to 512x512 instead of letterboxing",
    )
    parser.add_argument(
        "--save-normalized", action="store_true",
        help="Also save normalized .npy files during preprocessing",
    )
    parser.add_argument(
        "--skip-preprocessing", action="store_true",
        help="Skip preprocessing, run only analysis on existing metadata",
    )
    
    args = parser.parse_args()
    
    if args.skip_preprocessing:
        # Run only analysis on existing metadata
        run_existing_metadata(
            meta_dir=args.meta,
        )
    else:
        # Run full pipeline
        run_full_pipeline(
            input_path=args.input,
            output_dir=args.output,
            meta_dir=args.meta,
            keep_aspect=not args.stretch,
            save_normalized=args.save_normalized,
        )


if __name__ == "__main__":
    main()