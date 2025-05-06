# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2025 Francisco Rosa                                     *
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

"""This module implements the sun position configuration."""


import FreeCAD as App
from Render.virtualenv import RENDER_VENV_DIR
PATHPYTHON = RENDER_VENV_DIR
from ladybug.location import Location # https://www.ladybug.tools/ladybug/docs/_modules/ladybug/location.html
from ladybug.sunpath import Sunpath # https://www.ladybug.tools/ladybug/docs/_modules/ladybug/sunpath.html


## Sun position
def getSunPosition():
    obj = App.ActiveDocument.SunskyLight
    cit = obj.City
    cou = obj.Country
    lat = obj.Latitude
    lon = obj.Longitude
    tz = obj.TimeZone
    elev = 0
    mon = obj.Month
    da = obj.Day
    tim = obj.Time

    # Sun light coordinates:
    location_data = Location(city = cit, country = cou, latitude = lat, longitude = lon, time_zone = tz, elevation = elev)
    sp = Sunpath.from_location(location_data)
    # North angle of the sunpath in degrees. This is only used to adjust the sun_vector and does not affect the sun altitude or azimuth.
    nor = obj.North
    sp._north_angle = nor
    sun = sp.calculate_sun(month=mon, day=da, hour=tim) # Obs.: hours in decimal values
    sun_coordinates = sun.position_3d(radius = float(obj.Distance))
    obj.SunDirection = (sun_coordinates[0], sun_coordinates[1], sun_coordinates[2])
    # print(sun_coordinates)

    # Altitude and Azimute:
    obj.Altitude = sun.altitude
    obj.Azimuth = sun.azimuth
    # print('altitude: {}, azimuth: {}'.format(sun.altitude, sun.azimuth))

    ## Sunrise, noon and sunset:
    sunrise_sunset = sp.calculate_sunrise_sunset(month=mon, day = da)
    obj.Noon = str(sunrise_sunset ['noon'])
    obj.Sunrise = str(sunrise_sunset ['sunrise'])
    obj.Sunset = str(sunrise_sunset ['sunset'])
    # print('sunrise:{}, sunset:{}'.format(sunrise,sunset))

    # Daylight hours:
    obj.DaylightHours = str((sunrise_sunset ['sunset'] - sunrise_sunset ['sunrise']))
    # print(obj.DaylightHours)

