# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2025 Howetuft <howetuft@gmail.com>                      *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2.1 of   *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""This module computes sun position from geo coordinates.

It relies on ladybug (https://github.com/ladybug-tools/ladybug), which is
hosted in Render's virtualenv.
"""

from pathlib import Path
import json
import subprocess

import FreeCAD as App

from Render.constants import PKGDIR
from Render.virtualenv import run_script

LADYBUG_MODULE = "render_ladybug"  # Relative to package dir


def sunposition(
    city, country, latitude, longitude, time_zone, elevation, datetime
):
    """Compute sun position from earth location."""

    city = str(city)
    country = str(country)
    latitude = float(latitude)
    longitude = float(longitude)
    time_zone = float(time_zone)
    elevation = float(elevation)
    datetime = str(datetime)

    options = [
        city,
        country,
        str(latitude),
        str(longitude),
        str(time_zone),
        str(elevation),
        datetime,
    ]
    try:
        res = run_script(LADYBUG_MODULE, options)
    except subprocess.CalledProcessError as err:
        App.Console.PrintError(err)
        App.Console.PrintError("\n")
        App.Console.PrintError(err.output)
        raise

    print(res)  # TODO
    return json.loads(res)
