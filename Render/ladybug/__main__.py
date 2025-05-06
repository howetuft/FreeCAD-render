"""
This script is licensed under the GNU Lesser General Public License v2.1.
You should have received a copy of the GNU Lesser General Public License
along with this script. If not, see <https://www.gnu.org/licenses/>.

Copyright (C) 2025 Howetuft & Francisco Rosa
"""

import argparse
import json
from datetime import datetime
from ladybug.location import Location
from ladybug.sunpath import Sunpath


def parse_arguments():
    """
    Parse command-line arguments for sun coordinate computation.

    Returns:
        argparse.Namespace: Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Compute sun coordinates using ladybug-core."
    )
    parser.add_argument("city", type=str, help="City name")
    parser.add_argument("country", type=str, help="Country name")
    parser.add_argument(
        "latitude", type=float, help="Latitude of the location"
    )
    parser.add_argument(
        "longitude", type=float, help="Longitude of the location"
    )
    parser.add_argument(
        "time_zone",
        type=float,
        help="Time zone of the location (e.g., -5 for EST)",
    )
    parser.add_argument(
        "elevation", type=float, help="Elevation above sea level in meters"
    )
    parser.add_argument(
        "datetime",
        type=str,
        help="Date and time in ISO 8601 format (e.g., '2025-05-06T19:12:05')",
    )
    return parser.parse_args()


def compute_sun_coordinates(args):
    """
    Compute the sun's coordinates (altitude and azimuth) for a given location
    and time using ladybug-core.

    Args:
        args (argparse.Namespace): Parsed command-line arguments.

    Returns:
        dict: Solar coordinates with altitude and azimuth.
    """
    try:
        datetime_obj = datetime.fromisoformat(args.datetime)
    except ValueError:
        raise ValueError(
            "Invalid datetime format. Please use ISO 8601 format like "
            "'2025-05-06T19:12:05'."
        )

    location = Location(
        args.city,
        args.country,
        args.latitude,
        args.longitude,
        args.time_zone,
        args.elevation,
    )
    sunpath = Sunpath(location)
    solar_position = sunpath.calculate_sun(datetime_obj)

    return {
        "solar_coordinates": {
            "altitude": solar_position.altitude,
            "azimuth": solar_position.azimuth,
        }
    }


def main():
    """
    Main function to parse arguments, compute sun coordinates, and print the
    output in JSON format.
    """
    args = parse_arguments()
    try:
        result = compute_sun_coordinates(args)
        print(json.dumps(result, indent=4))
    except ValueError as error:
        print(error)


if __name__ == "__main__":
    main()
