# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2017 Yorik van Havre <yorik@uncreated.net>              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
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

"""Luxrender renderer plugin for FreeCAD Render workbench."""

# CAVEAT: THIS RENDERER PLUGIN IS DEPRECATED, DO NOT SPEND TIME ON IT...
#
# This LuxRender plugin has been deprecated and replaced by a new LuxCore
# module, as LuxRender software has been deprecated in favor of LuxCore.
# Therefore, this module should not be maintained...


# Suggested links to renderer documentation:
# NOTE As LuxRender has been deprecated in favor of LuxCore, LuxRender's
# documentation is quite rare. Best documentation seems to be found directly in
# the code of LuxCore importer for LuxRender, by reverse engineering,
# and additionally in LuxCore's documentation. Here are links to such
# resources:
# https://github.com/LuxCoreRender/LuxCore/blob/master/src/luxcore/luxparser/luxparse.cpp
# https://wiki.luxcorerender.org/LuxCore_SDL_Reference_Manual_v2.3

import os
import re
import shlex
from tempfile import mkstemp
from subprocess import Popen
from textwrap import dedent

import FreeCAD as App

TEMPLATE_FILTER = "LuxRender templates (luxrender_*.lxs)"


# ===========================================================================
#                             Write functions
# ===========================================================================

# CAVEAT: THIS RENDERER PLUGIN IS DEPRECATED, DO NOT WASTE TIME WITH IT...

def write_mesh(name, mesh, material):
    """Compute a string in renderer SDL to represent a FreeCAD mesh."""
    # Minimal material support
    color = material.default_color

    points = [f"{v.x} {v.y} {v.z}" for v in mesh.Topology[0]]
    norms = [f"{n.x} {n.y} {n.z}" for n in mesh.getPointNormals()]
    tris = [f"{t[0]} {t[1]} {t[2]}" for t in mesh.Topology[1]]

    snippet = """
    # Generated by FreeCAD (http://www.freecadweb.org/)
    MakeNamedMaterial "{name}_mat"
        "color Kd"              [{colo[0]} {colo[1]} {colo[2]}]
        "float sigma"           [0.2]
        "string type"           ["matte"]

    AttributeBegin  # {name}
    Transform [1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1]
    NamedMaterial "{name}_mat"
    Shape "mesh"
        "integer triindices" [{inds}]
        "point P" [{pnts}]
        "normal N" [{nrms}]
        "bool generatetangents" ["false"]
        "string name" ["{name}"]
    AttributeEnd  # {name}
    """

    return dedent(snippet).format(name=name,
                                  colo=color,
                                  inds=" ".join(tris),
                                  pnts=" ".join(points),
                                  nrms=" ".join(norms))


def write_camera(name, pos, updir, target):
    """Compute a string in renderer SDL to represent a camera."""
    # This is where you create a piece of text in the format of
    # your renderer, that represents the camera.

    snippet = """
    # Generated by FreeCAD (http://www.freecadweb.org/)
    # Declares position and view direction (camera '{0}')
    LookAt   {1.x} {1.y} {1.z}   {2.x} {2.y} {2.z}   {3.x} {3.y} {3.z}
    \n"""

    return dedent(snippet).format(name, pos.Base, target, updir)


def write_pointlight(name, pos, color, power):
    """Compute a string in renderer SDL to represent a point light."""
    # This is where you write the renderer-specific code
    # to export the point light in the renderer format

    # From Luxcore doc:
    # power is in watts
    # efficency (sic) is in lumens/watt
    efficency = 15  # incandescent light bulb ratio (average)
    gain = 10  # Guesstimated! (don't hesitate to propose more sensible values)

    snippet = """
    # Generated by FreeCAD (http://www.freecadweb.org)
    AttributeBegin # {n}
    Transform [1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1]
    LightSource "point"
         "float from"        [{f.x} {f.y} {f.z}]
         "color L"           [{L[0]} {L[1]} {L[2]}]
         "float power"       [{p}]
         "float efficency"   [{e}]
         "float gain"        [{g}]
    AttributeEnd # {n}
    \n"""

    return dedent(snippet).format(n=name,
                                  f=pos,
                                  L=color,
                                  p=power,
                                  e=efficency,
                                  g=gain)


def write_arealight(name, pos, size_u, size_v, color, power):
    """Compute a string in renderer SDL to represent an area light."""
    efficency = 15
    gain = 10  # Guesstimated!

    # We have to transpose 'pos' to make it fit for Lux
    # As 'transpose' method is in-place, we first make a copy
    placement = App.Matrix(pos.toMatrix())
    placement.transpose()
    trans = ' '.join([str(a) for a in placement.A])

    snippet = """
    # Generated by FreeCAD (http://www.freecadweb.org)
    AttributeBegin # {n}
    Transform [{t}]
    # NamedMaterial "{n}_mat"
    AreaLightSource "area"
        "color L"           [{L[0]} {L[1]} {L[2]}]
        "float power"       [{p}]
        "float efficency"   [{e}]
        "float importance"  [1.000000000000000]
        "float gain"        [{g}]
    Shape "mesh"
        "point P" [-{u} -{v} 0.0 {u} -{v} 0.0 {u} {v} 0.0 -{u} {v} 0.0]
        "integer triindices" [0 1 2 0 2 3]
        "bool generatetangents" ["false"]
        "string name" ["{n}"]
    AttributeEnd # {n}
    \n"""
    return dedent(snippet).format(n=name,
                                  t=trans,
                                  L=color,
                                  p=power,
                                  e=efficency,
                                  g=gain,
                                  u=size_u / 2,
                                  v=size_v / 2,
                                  )


def write_sunskylight(name, direction, distance, turbidity, albedo):
    """Compute a string in renderer SDL to represent a sunsky light."""
    snippet = """
    # Generated by FreeCAD (http://www.freecadweb.org)
    AttributeBegin # {n}

    LightGroup "Sunlight"

    LightSource "sunsky2"
            "float gain" [1.0]
            "float importance" [2.0]
            "integer nsamples" [1]
            "float turbidity" [{t}]
            "vector sundir" [{d.x} {d.y} {d.z}]

    AttributeEnd # {n}
    \n"""
    return dedent(snippet).format(n=name,
                                  t=turbidity,
                                  d=direction)


# ===========================================================================
#                              Render function
# ===========================================================================


# CAVEAT: THIS RENDERER PLUGIN IS DEPRECATED, DO NOT SPEND TIME ON IT...


def render(project, prefix, external, output, width, height):
    """Run renderer.

    Args:
        project -- The project to render
        prefix -- A prefix string for call (will be inserted before path to
            renderer)
        external -- A boolean indicating whether to call UI (true) or console
            (false) version of renderder
        width -- Rendered image width, in pixels
        height -- Rendered image height, in pixels

    Returns:
        A path to output image file
    """
    # Here you trigger a render by firing the renderer
    # executable and passing it the needed arguments, and
    # the file it needs to render

    msg = "WARNING: LuxRender renderer is DEPRECATED and will no longer be "\
          "maintained. You should consider transferring your rendering "\
          "project to LuxCore renderer...\n"
    App.Console.PrintWarning(msg)

    # change image size in template
    with open(project.PageResult, "r", encoding="utf-8") as f:
        template = f.read()

    res = re.findall("integer xresolution", template)
    if res:
        template = re.sub(r'"integer xresolution".*?\[.*?\]',
                          f'"integer xresolution" [{width}]',
                          template)

    res = re.findall("integer yresolution", template)
    if res:
        template = re.sub(r'"integer yresolution".*?\[.*?\]',
                          f'"integer yresolution" [{height}]',
                          template)

    if res:
        f_handle, f_path = mkstemp(
            prefix=project.Name,
            suffix=os.path.splitext(project.Template)[-1])
        os.close(f_handle)
        with open(f_path, "w", encoding="utf-8") as f:
            f.write(template)
        project.PageResult = f_path
        os.remove(f_path)
        App.ActiveDocument.recompute()

    params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Render")
    args = params.GetString("LuxParameters", "")
    rpath = params.GetString("LuxRenderPath" if external
                             else "LuxConsolePath", "")
    if not rpath:
        App.Console.PrintError("Unable to locate renderer executable. "
                               "Please set the correct path in "
                               "Edit -> Preferences -> Render\n")
        return

    # Call Luxrender
    cmd = prefix + rpath + " " + args + " " + project.PageResult + "\n"
    App.Console.PrintMessage(cmd)
    try:
        Popen(shlex.split(cmd))
    except OSError as err:
        msg = "Luxrender call failed: '" + err.strerror + "'\n"
        App.Console.PrintError(msg)

    return
