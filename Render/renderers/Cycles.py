# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2019 Yorik van Havre <yorik@uncreated.net>              *
# *   Copyright (c) 2022 Howefuft <howetuft-at-gmail>                       *
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

"""Cycles renderer plugin for FreeCAD Render workbench."""

# Suggested documentation links:
# NOTE Standalone Cycles is experimental, so no documentation is available.
# Instead, documentation must be searched directly in code (via reverse
# engineering), and in the examples provided with it.
# Here are some links:
# https://wiki.blender.org/wiki/Source/Render/Cycles/Standalone
# https://developer.blender.org/diffusion/C/browse/master/src/
# https://developer.blender.org/diffusion/C/browse/master/src/render/nodes.cpp
# https://developer.blender.org/diffusion/C/browse/master/src/app/cycles_xml.cpp
# https://developer.blender.org/diffusion/C/browse/master/examples/
#
# A few hints (my understanding of cycles_standalone):
#
# The 'int main()' is in 'src/app/cycles_standalone.cpp' (but you may not be
# most interested in it)
#
# The xml input file is processed by 'src/app/cycles_xml.cpp' functions.
# The entry point is 'xml_read_file', which cascades to 'xml_read_scene' via
# 'xml_read_include' function.
#
# 'xml_read_scene' is a key function to study: it recognizes and dispatches all
# the possible nodes to 'xml_read_*' node-specialized parsing functions.
# A few more 'xml_read_*' (including 'xml_read_node' are defined in
# /src/graph/node_xml.cpp


import pathlib
import functools
from math import degrees, asin, sqrt, radians, atan2, acos

import FreeCAD as App

from .utils.sunlight import sunlight

TEMPLATE_FILTER = "Cycles templates (cycles_*.xml)"


# ===========================================================================
#                             Objects
# ===========================================================================


def write_mesh(name, mesh, material, vertex_normals=False):
    """Compute a string in renderer SDL to represent a FreeCAD mesh."""
    # Compute material values
    matval = material.get_material_values(
        name, _write_texture, _write_value, _write_texref
    )

    snippet_mat = _write_material(name, matval)

    points = [_write_point(p) for p in mesh.Topology[0]]
    points = "  ".join(points)
    verts = [f"{v[0]} {v[1]} {v[2]}" for v in mesh.Topology[1]]
    verts = "  ".join(verts)
    nverts = ["3"] * len(mesh.Topology[1])
    nverts = "  ".join(nverts)
    norms = [f"{n[0]} {n[1]} {n[2]}" for n in mesh.getPointNormals()]
    norms = "  ".join(norms)

    if mesh.has_uvmap():
        uvs = [f"{p.x} {p.y}" for p in mesh.uvmap_per_vertex()]
        uvs = "  ".join(uvs)
        uv_statement = f'    UV="{uvs}"\n'
    else:
        uv_statement = ""

    snippet_obj = (
        f"""
<state shader="{name}">
<mesh
    P="{points}"
    N="{norms}"
    verts="{verts}"
    nverts="{nverts}"
{uv_statement}/>
</state>
"""
        if vertex_normals
        else f"""
<state shader="{name}">
<mesh
    P="{points}"
    verts="{verts}"
    nverts="{nverts}"
{uv_statement}/>
</state>
"""
    )

    snippet = snippet_mat + snippet_obj

    return snippet


def write_camera(name, pos, updir, target, fov):
    """Compute a string in renderer SDL to represent a camera."""
    # This is where you create a piece of text in the format of
    # your renderer, that represents the camera.

    # Cam rotation is angle(deg) axisx axisy axisz
    # Scale needs to have z inverted to behave like a decent camera.
    # No idea what they have been doing at blender :)
    snippet = f"""
<!-- Generated by FreeCAD - Camera '{name}' -->
<transform
    rotate="{_write_rotation(pos.Rotation)}"
    translate="{_write_vec(pos.Base)}"
    scale="1 1 -1" >
    <camera
        type="perspective"
        fov="{_write_float(radians(fov))}"
    />
</transform>"""

    return snippet


def write_pointlight(name, pos, color, power):
    """Compute a string in renderer SDL to represent a point light."""
    # This is where you write the renderer-specific code
    # to export a point light in the renderer format

    snippet = f"""
<!-- Generated by FreeCAD - Pointlight '{name}' -->
<shader name="{name}_shader">
<emission
    name="{name}_emit"
    color="{_write_color(color)}"
    strength="{_write_float(power * 100)}"
/>
<connect from="{name}_emit emission" to="output surface"/>
</shader>
<state shader="{name}_shader">
<light
    name="{name}"
    light_type="point"
    co="{_write_point(pos)}"
    strength="1 1 1"
/>
</state>
"""

    return snippet


def write_arealight(name, pos, size_u, size_v, color, power, transparent):
    """Compute a string in renderer SDL to represent an area light."""
    strength = power if transparent else power / (size_u * size_v)
    strength /= 100
    write_strength = _write_float(strength)

    if transparent:
        # Transparent area light
        rot = pos.Rotation
        axis1 = rot.multVec(App.Vector(1.0, 0.0, 0.0))
        axis2 = rot.multVec(App.Vector(0.0, 1.0, 0.0))
        direction = axis1.cross(axis2)
        snippet = f"""
<!-- Generated by FreeCAD - Area light '{name}' (transparent) -->
<shader name="{name}_shader">
<emission
    name="{name}_emit"
    color="{_write_color(color)}"
    strength="{_write_float(strength)}"
/>
<connect from="{name}_emit emission" to="output surface"/>
</shader>
<state shader="{name}_shader">
<light
    type="area"
    co="{_write_point(pos.Base)}"
    strength="{write_strength} {write_strength} {write_strength}"
    axisu="{_write_vec(axis1)}"
    axisv="{_write_vec(axis2)}"
    sizeu="{_write_float(size_u)}"
    sizev="{_write_float(size_v)}"
    size="0.0"
    round="false"
    dir="{_write_vec(direction)}"
    use_mis = "true"
/>
</state>"""

    else:
        # Opaque area light (--> mesh light)
        points = [
            (-size_u / 2, -size_v / 2, 0),
            (+size_u / 2, -size_v / 2, 0),
            (+size_u / 2, +size_v / 2, 0),
            (-size_u / 2, +size_v / 2, 0),
        ]
        points = [pos.multVec(App.Vector(*p)) for p in points]
        points = [_write_point(p) for p in points]
        points = "  ".join(points)

        snippet = f"""
<!-- Generated by FreeCAD - Area light '{name}' (opaque) -->
<shader name="{name}_shader" use_mis="true">
<emission
    name="{name}_emit"
    color="{_write_color(color)}"
    strength="{_write_float(strength)}"
/>
<connect from="{name}_emit emission" to="output surface"/>
</shader>
<state shader="{name}_shader">
<mesh
    P="{points}"
    nverts="4"
    verts="0 1 2 3"
    use_mis="true"
/>
</state>
"""

    return snippet


def write_sunskylight(name, direction, distance, turbidity, albedo):
    """Compute a string in renderer SDL to represent a sunsky light."""
    return _write_sunskylight_nishita(name, direction, distance, turbidity, albedo)


def _write_sunskylight_hosekwilkie(name, direction, distance, turbidity, albedo):
    """Compute a string in renderer SDL to represent a sunsky light."""
    # We model sun_sky with a sun light and a sky texture for world

    # For sky texture, direction must be normalized
    assert direction.Length
    _dir = App.Vector(direction)
    _dir.normalize()
    theta = acos(_dir.z / sqrt(_dir.x**2 + _dir.y**2 + _dir.z**2))
    sun = sunlight(theta, turbidity)
    rgb = sun.xyz.to_srgb_with_fixed_luminance(1.)

    # Strength for sun. Should be 1.0, but everything is burnt
    strength = 0.5
    # Sun angle as seen from earth: 0.5°
    angle = radians(0.5)

    snippet_sky = f"""
<!-- Generated by FreeCAD - Sun_sky light '{name}' -->
<shader name="{name}_bg_shader">
    <background name="{name}_bg" strength="5.0"/>
    <connect from="{name}_bg background" to="output surface" />
    <sky_texture
        name="{name}_tex"
        sky_type="hosek_wilkie"
        turbidity="{turbidity}"
        sun_direction="{_dir.x}, {_dir.y}, {_dir.z}"
        ground_albedo="{albedo}"
    />
    <connect from="{name}_tex color" to="{name}_bg color" />
</shader>
<background shader="{name}_bg_shader" />
"""
    snippet_sun = f"""\
<shader name="{name}_shader">
    <emission name="{name}_emit"
        color="{rgb[0]} {rgb[1]} {rgb[2]}"
        strength="{sun.irradiance}"
    />
    <connect from="{name}_emit emission" to="output surface"/>
</shader>
<state shader="{name}_shader">
    <light
        type="distant"
        co="1 1 1"
        strength="{strength} {strength} {strength}"
        dir="{-_dir.x} {-_dir.y} {-_dir.z}"
        angle="{angle}"
    />
</state>
"""
    return "".join([snippet_sky, snippet_sun])


def _write_sunskylight_nishita(name, direction, distance, turbidity, albedo):
    """Compute a string in renderer SDL to represent a sunsky light."""
    # We use the new improved nishita model (2020)

    assert direction.Length
    _dir = App.Vector(direction)
    _dir.normalize()
    theta = asin(_dir.z / sqrt(_dir.x**2 + _dir.y**2 + _dir.z**2))
    phi = atan2(_dir.x, _dir.y)

    snippet_shader = f"""
<!-- Generated by FreeCAD - Sun_sky light '{name}' -->
<shader name="{name}_shader">
    <sky_texture
        name="{name}_tex"
        sky_type="nishita_improved"
        turbidity="{_write_float(turbidity)}"
        ground_albedo="{_write_float(albedo)}"
        sun_disc="true"
        sun_elevation="{_write_float(theta)}"
        sun_rotation="{_write_float(phi)}"
        sun_size="{radians(0.545)}"
        sun_intensity="1"
        altitude="500"
    />
    <emission
        name="{name}_emit"
        strength="0.2"
    />
    <connect from="{name}_tex color" to="{name}_emit color" />
    <connect from="{name}_emit emission" to="output surface" />
</shader>"""

    snippet_sun = f"""
<state shader="{name}_shader">
<light
    light_type="background"
    strength="1 1 1"
    use_mis="true"
/>
</state>"""

    snippet_sky = f"""
<background shader="{name}_shader"/>
"""

    return "".join([snippet_shader, snippet_sun, snippet_sky])


def write_imagelight(name, image):
    """Compute a string in renderer SDL to represent an image-based light."""
    # Caveat: Cycles requires the image file to be in the same directory
    # as the input file
    filename = pathlib.Path(image).name
    snippet = f"""
<!-- Generated by FreeCAD - Image-based light '{name}' -->
<background>
    <background name="{name}_bg" />
    <environment_texture
        name= "{name}_tex"
        filename = "{filename}"
    />
    <connect from="{name}_tex color" to="{name}_bg color" />
    <connect from="{name}_bg background" to="output surface" />
</background>
"""
    return snippet


# ===========================================================================
#                              Material implementation
# ===========================================================================


def _write_material(name, matval):
    """Compute a string in the renderer SDL, to represent a material.

    This function should never fail: if the material is not recognized,
    a fallback material is provided.
    """
    # Bsdf
    shadertype = matval.shadertype
    try:
        material_function = MATERIALS[shadertype]
    except KeyError:
        # Unknown shader - fallback
        msg = (
            "'{}' - Material '{}' unknown by renderer, using fallback "
            "material\n"
        )
        App.Console.PrintWarning(msg.format(name, shadertype))
        snippet_mat = _write_material_fallback(name, matval)
        return f"""
<!-- Generated by FreeCAD - Shader 'Fallback' - Object '{name}' -->
<shader name="{name}">
{snippet_mat}
</shader>
"""

    # Get material snippet
    snippet_mat = material_function(name, matval)

    # Textures
    snippet_tex = matval.write_textures()

    # Add bump node (for bump and normal...) to textures
    # if necessary...
    if matval.has_bump() or matval.has_normal():
        bump_snippet = f"""
<bump
    name="{name}_bump"
    use_object_space = "true"
    invert = "false"
    distance = "1.0"
    strength = "1.0"
/>
<connect from="{name}_bump normal" to="{name}_bsdf normal"/>"""

        snippet_tex = f"""\
{bump_snippet}
{snippet_tex}"""

    # Final result
    snippet_shader = f"""
<!-- Generated by FreeCAD - Shader '{shadertype}' - Object '{name}' -->
<shader name="{name}">
{snippet_mat}
{snippet_tex}
</shader>
"""

    return snippet_shader


def _write_material_passthrough(name, matval):
    """Compute a string in the renderer SDL for a passthrough material."""
    # snippet = indent(material.passthrough.string, "    ")
    snippet = matval["string"]
    return snippet.format(n=name, c=matval.default_color)


def _write_material_glass(name, matval, connect_to="output surface"):
    """Compute a string in the renderer SDL for a glass material."""
    return f"""
<glass_bsdf
    name="{name}_bsdf"
    IOR="{matval["ior"]}"
    color="{matval["color"]}"
/>
<connect from="{name}_bsdf bsdf" to="{connect_to}"/>"""


def _write_material_disney(name, matval, connect_to="output surface"):
    """Compute a string in the renderer SDL for a Disney material."""
    return f"""
<principled_bsdf
    name="{name}_bsdf"
    base_color = "{matval["basecolor"]}"
    subsurface = "{matval["subsurface"]}"
    metallic = "{matval["metallic"]}"
    specular = "{matval["specular"]}"
    specular_tint = "{matval["speculartint"]}"
    roughness = "{matval["roughness"]}"
    anisotropic = "{matval["anisotropic"]}"
    sheen = "{matval["sheen"]}"
    sheen_tint = "{matval["sheentint"]}"
    clearcoat = "{matval["clearcoat"]}"
    clearcoat_roughness = "{1 - float(matval["clearcoatgloss"])}"
/>
<connect from="{name}_bsdf bsdf" to="{connect_to}"/>"""


def _write_material_pbr(name, matval, connect_to="output surface"):
    """Compute a string in the renderer SDL for a Disney material."""
    return f"""
<principled_bsdf
    name="{name}_bsdf"
    base_color = "{matval["basecolor"]}"
    roughness = "{matval["roughness"]}"
    metallic = "{matval["metallic"]}"
    specular = "0.5"
/>
<connect from="{name}_bsdf bsdf" to="{connect_to}"/>"""


def _write_material_diffuse(name, matval, connect_to="output surface"):
    """Compute a string in the renderer SDL for a Diffuse material."""
    return f"""
<diffuse_bsdf name="{name}_bsdf" color="{matval["color"]}"/>
<connect from="{name}_bsdf bsdf" to="{connect_to}"/>"""


def _write_material_mixed(name, matval, connect_to="output surface"):
    """Compute a string in the renderer SDL for a Mixed material."""
    # Glass
    submat_g = matval.getmixedsubmat("glass")
    snippet_g = _write_material_glass(
        f"{name}_glass", submat_g, f"{name}_bsdf closure2"
    )
    snippet_g_tex = submat_g.write_textures()

    # Diffuse
    submat_d = matval.getmixedsubmat("diffuse")
    snippet_d = _write_material_diffuse(
        f"{name}_diffuse", submat_d, f"{name}_bsdf closure1"
    )
    snippet_d_tex = submat_d.write_textures()

    # Mix
    snippet_m = f"""
<mix_closure name="{name}_bsdf" fac="{matval["transparency"]}" />
<connect from="{name}_bsdf closure" to="{connect_to}" />"""

    return snippet_m + snippet_g + snippet_d + snippet_g_tex + snippet_d_tex


def _write_material_carpaint(name, matval, connect_to="output surface"):
    """Compute a string in the renderer SDL for a carpaint material."""
    return f"""
<!-- Main: principled with coating -->
<principled_bsdf
    name="{name}_bsdf"
    base_color = "{matval["basecolor"]}"
    specular = "0.1"
    roughness = "0.5"
    clearcoat = "1.0"
    clearcoat_roughness = "0.05"
/>
<connect from="{name}_bsdf bsdf" to="{connect_to}"/>

<!-- ColorRamp for noise -->
<rgb_ramp
    name="{name}_noiseramp"
    ramp="1.0 1.0 1.0 0.0 0.0 0.0"
    ramp_alpha="0.0 0.4"
/>
<connect from="{name}_noiseramp color" to="{name}_main metallic"/>

<!-- Noise -->
<noise_texture
    name="{name}_noise"
    dimensions="3D"
    scale="1000000"
    detail="5"
/>
<connect from="{name}_noise fac" to="{name}_noiseramp fac"/>"""


def _write_material_fallback(name, matval):
    """Compute a string in the renderer SDL for a fallback material.

    Fallback material is a simple Diffuse material.
    """
    try:
        red = float(matval.default_color.r)
        grn = float(matval.default_color.g)
        blu = float(matval.default_color.b)
        assert (0 <= red <= 1) and (0 <= grn <= 1) and (0 <= blu <= 1)
    except (AttributeError, ValueError, TypeError, AssertionError):
        red, grn, blu = 1, 1, 1
    snippet = """
<diffuse_bsdf name="{n}_bsdf" color="{r}, {g}, {b}"/>
<connect from="{n}_bsdf bsdf" to="output surface"/>"""
    return snippet.format(n=name, r=red, g=grn, b=blu)


MATERIALS = {
    "Passthrough": _write_material_passthrough,
    "Glass": _write_material_glass,
    "Disney": _write_material_disney,
    "Diffuse": _write_material_diffuse,
    "Mixed": _write_material_mixed,
    "Carpaint": _write_material_carpaint,
    "Substance_PBR": _write_material_pbr,
}

# ===========================================================================
#                             Textures
# ===========================================================================

# Mapping between shader fields and sockets to connect texture to
SOCKET_MAPPING = {
    "ior": "IOR",
    "basecolor": "base_color",
    "speculartint": "specular_tint",
    "sheentint": "sheen_tint",
    "transparency": "fac",
}


def _write_texture(**kwargs):
    """Compute a string in renderer SDL to describe a texture.

    The texture is computed from a property of a shader (as the texture is
    always integrated into a shader). Property's data are expected as
    arguments.

    Args:
        objname -- Object name for which the texture is computed
        propname -- Name of the shader property
        propvalue -- Value of the shader property

    Returns:
        the name of the texture
        the SDL string of the texture
    """
    # Retrieve parameters
    objname = kwargs["objname"]
    propname = kwargs["propname"]
    propvalue = kwargs["propvalue"]

    # Compute socket name (by default, it should yield propname...)
    socket = SOCKET_MAPPING.get(propname, propname)

    # Compute texture name
    texname = f"{objname}_{propname}_tex"

    # Compute file name
    # Caveat: Cycles requires the image file to be in the same directory
    # as the input file
    filename = pathlib.Path(propvalue.file).name

    scale, rotation = float(propvalue.scale), float(propvalue.rotation)
    translation_u = float(propvalue.translation_u)
    translation_v = float(propvalue.translation_v)

    # https://blender.stackexchange.com/questions/16443/using-a-normal-map-together-with-a-bump-map

    if propname == "bump":
        colorspace = "__builtin_raw"
        connect = f"""
<connect from="{texname} color" to="{objname}_bump height"/>"""

    elif propname == "normal":
        colorspace = "__builtin_raw"
        # We use blender space, but we have to flip z
        # "strange blender convention"
        # https://github.com/blender/cycles/blob/master/src/kernel/svm/tex_coord.h#L324
        connect = f"""
<rgb_curves
    name="{texname}_curve"
    curves="0.0 0.0 1.0  1.0 1.0 0.5"
    fac = "1.0"
/>
<normal_map
    name="{texname}_normalmap"
    space="blender_object"
    strength="0.2"
/>
<connect from="{texname} color" to="{texname}_curve value"/>
<connect from="{texname}_curve value" to="{texname}_normalmap color"/>
<connect from="{texname}_normalmap normal" to="{objname}_bump normal"/>"""

    elif propname == "displacement":
        colorspace = "__builtin_raw"
        connect = f"""
<normal_map
    name="{texname}_normalmap_disp"
    space="object"
    strength="0.2"
/>
<connect from="{texname} color" to="output displacement"/>"""

    elif propname == "clearcoatgloss":
        colorspace = "__builtin_raw"
        connect = f"""
<math
    name="{texname}_clearcoat_roughness"
    math_type="subtract"
    value1="1.0"
/>
<connect
    from="{texname} color"
    to="{texname}_clearcoat_roughness value2"
/>
<connect
    from="{texname}_clearcoat_roughness value"
    to="{objname}_bsdf clearcoat_roughness"
/>"""

    else:
        # Plain texture
        colorspace = (
            "__builtin_srgb" if "color" in propname else "__builtin_raw"
        )
        connect = f"""
<connect from="{texname} color" to="{objname}_bsdf {socket}"/>"""

    texture_core = f"""
<image_texture
    name="{texname}"
    filename="{filename}"
    colorspace="{colorspace}"
    tex_mapping.scale="{scale} {scale} {scale}"
    tex_mapping.rotation="{rotation} {rotation} {rotation}"
    tex_mapping.translation="{translation_u} {translation_v} 0.0"
/>"""

    return texname, texture_core + connect


def _write_value(**kwargs):
    """Compute a string in renderer SDL from a shader property value.

    Args:
        proptype -- Shader property's type
        propvalue -- Shader property's value

    The result depends on the type of the value...
    """
    # Retrieve parameters
    proptype = kwargs["proptype"]
    val = kwargs["propvalue"]

    # Snippets for values
    if proptype == "RGB":
        value = f"{_rnd(val.r)} {_rnd(val.g)} {_rnd(val.b)}"
    elif proptype == "float":
        value = f"{_rnd(val)}"
    elif proptype == "node":
        value = ""
    elif proptype == "RGBA":
        value = f"{_rnd(val.r)} {_rnd(val.g)} {_rnd(val.b)} {_rnd(val.a)}"
    elif proptype == "texonly":
        value = f"{val}"
    elif proptype == "str":
        value = f"{val}"
    else:
        raise NotImplementedError

    return value


def _write_texref(**kwargs):  # pylint: disable=unused-argument
    """Compute a string in SDL for a reference to a texture in a shader."""
    return "0.0"  # In Cycles, there is no reference to textures in shaders...


# ===========================================================================
#                              Helpers
# ===========================================================================


_rnd = functools.partial(round, ndigits=8)  # Round to 8 digits (helper)

_write_float = _rnd


def _write_point(pnt):
    """Write a point."""
    return f"{_rnd(pnt.x)} {_rnd(pnt.y)} {_rnd(pnt.z)}"


_write_vec = _write_point  # Write a vector


def _write_rotation(rot):
    """Write a rotation."""
    return f"{_rnd(degrees(rot.Angle))} {_write_vec(rot.Axis)}"


def _write_color(col):
    """Write a color."""
    return f"{_rnd(col[0])} {_rnd(col[1])} {_rnd(col[2])}"


# ===========================================================================
#                              Render function
# ===========================================================================


def render(project, prefix, external, input_file, output_file, width, height):
    """Generate renderer command.

    Args:
        project -- The project to render
        prefix -- A prefix string for call (will be inserted before path to
            renderer)
        external -- A boolean indicating whether to call UI (true) or console
            (false) version of renderer
        input_file -- path to input file
        output -- path to output file
        width -- Rendered image width, in pixels
        height -- Rendered image height, in pixels

    Returns:
        The command to run renderer (string)
        A path to output image file (string)
    """
    # Here you trigger a render by firing the renderer
    # executable and passing it the needed arguments, and
    # the file it needs to render
    params = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Render")
    prefix = params.GetString("Prefix", "")
    if prefix:
        prefix += " "
    rpath = params.GetString("CyclesPath", "")
    args = params.GetString("CyclesParameters", "")
    args += f""" --output "{output_file}" """
    if not external:
        args += " --background"
    if not rpath:
        App.Console.PrintError(
            "Unable to locate renderer executable. "
            "Please set the correct path in "
            "Edit -> Preferences -> Render\n"
        )
        return None, None
    args += " --width " + str(width)
    args += " --height " + str(height)
    filepath = f'"{input_file}"'
    cmd = prefix + rpath + " " + args + " " + filepath

    return cmd, output_file
