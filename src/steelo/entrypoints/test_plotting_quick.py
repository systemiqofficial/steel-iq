#!/usr/bin/env python
"""
Quick script to test plotting functions with sample data.
Run this to verify plotting works without running a full simulation.

Usage:
    python -m steelo.entrypoints.test_plotting_quick [--use-real-csv CSV_FILE]
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
import argparse

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from steelo.adapters.dataprocessing.postprocessing.generate_post_run_plots import generate_post_run_cap_prod_plots  # noqa
from steelo.utilities import plotting  # noqa


def create_sample_data():
    """Create sample output dataframe that mimics real simulation output."""
    print("Creating sample data...")

    # Create sample data for years 2025-2030
    years = list(range(2025, 2031))
    technologies = ["BF-BOF", "DRI-EAF", "Scrap-EAF", "H2-DRI", "Innovative"]
    regions = ["EU + Schengen", "China", "India", "North America", "Middle East", "Africa", "Latin America"]
    locations = ["DEU", "CHN", "IND", "USA", "SAU", "ZAF", "BRA"]

    data = []
    for year_idx, year in enumerate(years):
        for tech_idx, tech in enumerate(technologies):
            for reg_idx, region in enumerate(regions):
                # Create realistic capacity progression
                base_capacity = 100 + tech_idx * 50 + reg_idx * 20
                capacity = base_capacity + year_idx * 10

                data.append(
                    {
                        "year": year,
                        "furnace_type": tech,
                        "technology": tech,
                        "region": region,
                        "location": locations[reg_idx % len(locations)],
                        "product": "steel",
                        "capacity": capacity,
                        "production": capacity * np.random.uniform(0.7, 0.95),
                        "direct_emissions": capacity * np.random.uniform(0.5, 2.0),
                        "unit_vopex": 200 + np.random.uniform(-50, 100),
                        "unit_fopex": 30 + np.random.uniform(-10, 20),
                        "furnace_group_id": f"FG_{tech_idx}_{reg_idx}_{year}",
                        "cumulative_capacity": capacity * (year_idx + 1),
                    }
                )

    # Add some iron data
    for year in years[:3]:  # Only first 3 years for iron
        for reg_idx, region in enumerate(regions[:4]):  # Only first 4 regions
            data.append(
                {
                    "year": year,
                    "furnace_type": "BF",
                    "technology": "BF",
                    "region": region,
                    "location": locations[reg_idx % len(locations)],
                    "product": "iron",
                    "capacity": 200 + reg_idx * 30,
                    "production": 180 + reg_idx * 25,
                    "direct_emissions": 100 + reg_idx * 15,
                    "unit_vopex": 150,
                    "unit_fopex": 25,
                    "furnace_group_id": f"FG_iron_{reg_idx}_{year}",
                    "cumulative_capacity": 1000 + reg_idx * 100,
                }
            )

    df = pd.DataFrame(data)
    print(f"Created sample data with {len(df)} rows, years {df['year'].min()}-{df['year'].max()}")
    return df


def test_plotting(csv_file=None):
    """Test plotting functions with sample or real data."""
    # Set up test output directory
    test_output_dir = project_root / "outputs" / "test_plots"
    test_output_dir.mkdir(parents=True, exist_ok=True)

    # Temporarily redirect plot output
    original_pam = plotting.PAM_PLOTS_DIR
    original_geo = plotting.GEO_PLOTS_DIR

    plotting.PAM_PLOTS_DIR = test_output_dir / "pam"
    plotting.GEO_PLOTS_DIR = test_output_dir / "geo"

    try:
        if csv_file:
            print(f"\nUsing real CSV file: {csv_file}")
            # Save to temp location that generate_post_run_cap_prod_plots expects
            temp_csv = test_output_dir / "test_data.csv"
            df = pd.read_csv(csv_file)
            df.to_csv(temp_csv, index=False)
        else:
            print("\nUsing generated sample data")
            df = create_sample_data()
            temp_csv = test_output_dir / "test_data.csv"
            df.to_csv(temp_csv, index=False)

            # Show sample of the data
            print("\nSample of data:")
            print(df.head())
            print(f"\nYears in data: {sorted(df['year'].unique())}")
            print(f"Technologies: {sorted(df['technology'].unique())}")
            print(f"Regions: {sorted(df['region'].unique())}")

        print("\nRunning plot generation...")
        print(f"Plots will be saved to: {test_output_dir}")

        # Run the plot generation
        generate_post_run_cap_prod_plots(temp_csv)

        print("\n✓ Plot generation completed successfully!")

        # List generated files
        plot_files = list(plotting.PAM_PLOTS_DIR.glob("*.png")) + list(plotting.GEO_PLOTS_DIR.glob("*.png"))
        print(f"\nGenerated {len(plot_files)} plot files:")
        for plot_file in sorted(plot_files):
            size_kb = plot_file.stat().st_size / 1024
            print(f"  - {plot_file.name} ({size_kb:.1f} KB)")

    except Exception as e:
        print(f"\n✗ Error during plotting: {str(e)}")
        import traceback

        traceback.print_exc()

    finally:
        # Restore original directories
        plotting.PAM_PLOTS_DIR = original_pam
        plotting.GEO_PLOTS_DIR = original_geo

    print(f"\nTest plots saved in: {test_output_dir}")
    print("You can view them to verify plotting is working correctly.")


def main():
    parser = argparse.ArgumentParser(description="Test plotting functions quickly")
    parser.add_argument(
        "--use-real-csv", type=str, help="Path to a real post-processed CSV file from a previous simulation"
    )

    args = parser.parse_args()
    test_plotting(args.use_real_csv)


if __name__ == "__main__":
    main()
