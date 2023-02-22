# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2023 Howetuft <howetuft@gmail.com>                      *
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

"""This module implements RenderMesh class.

RenderMesh is an extended version of FreeCAD Mesh.Mesh, designed for
rendering purpose.
"""

# Useful resources:
# https://www.pixyz-software.com/documentations/html/2021.1/studio/UVProjectionTool.html

import enum
import os
import tempfile
import itertools as it
import functools
import time
from math import pi, atan2, asin, isclose, radians, cos, sin, hypot
import runpy
import shutil
import copy

import cProfile
import pstats
import io
from pstats import SortKey

import FreeCAD as App
import Mesh

from Render.constants import PKGDIR, PARAMS
from Render.rendermesh_mp import vector3d


# ===========================================================================
#                               RenderMesh
# ===========================================================================


class RenderMesh:
    """An extended version of FreeCAD Mesh, designed for rendering.

    RenderMesh is based on Mesh.Mesh.
    In addition, RenderMesh implements:
    - UV map management
    - scaling, via _Transformation
    - an improved vertex normals computation, for autosmoothing
    """

    def __init__(
        self,
        mesh,
        autosmooth=True,
        split_angle=radians(30),
        compute_uvmap=False,
        uvmap_projection=None,
    ):
        """Initialize RenderMesh.

        Args:
            mesh -- a Mesh.Mesh object from which to initialize
            autosmooth -- flag to trigger autosmooth computation (bool)
            split_angle -- angle that breaks adjacency, for sharp edge
                (float, in radians)
            compute_uvmap -- flag to trigger uvmap computation (bool)
            uvmap_projection -- type of projection to use for uv map
                among "Cubic", "Spherical", "Cylindric"
        """
        self.debug = PARAMS.GetBool("Debug")

        # Create profile object (debug)
        if self.debug:
            prof = cProfile.Profile()
            prof.enable()

        # Check mandatory input
        if not mesh:
            raise ValueError()

        # Initialize
        self.__vnormals = []
        self.__uvmap = []

        # First we make a copy of the mesh, we separate mesh and
        # placement and we set the mesh at its origin (null placement)
        self.__originalmesh = mesh.copy()
        self.__originalmesh.Placement = App.Base.Placement()

        # We initialize self transformation
        self.__transformation = _Transformation(mesh.Placement)

        # Then we store the topology in internal structures
        points, facets = self.__originalmesh.Topology
        self.__points = [tuple(p) for p in points]
        self.__facets = facets
        self.__normals = [tuple(f.Normal) for f in self.__originalmesh.Facets]
        self.__areas = [f.Area for f in self.__originalmesh.Facets]

        # Multiprocessing preparation (if required)
        self.multiprocessing = False
        if (
            PARAMS.GetBool("EnableMultiprocessing")
            and self.count_points >= 2000
        ):
            python = _find_python()
            if python:
                self.multiprocessing = True
                self.python = python

        # Uvmap
        if compute_uvmap:
            msg = f"[Render][Object] Uv map '{uvmap_projection}'\n"
            App.Console.PrintLog(msg)
            self.compute_uvmap(uvmap_projection)
            assert self.has_uvmap()

        # Autosmooth
        if autosmooth:
            App.Console.PrintLog(f"[Render][Object] Autosmooth\n")
            self.separate_connected_components(split_angle)
            self.compute_vnormals()
            self.__autosmooth = True
        else:
            self.__autosmooth = False

        # Print profile stats (debug)
        if self.debug:
            prof.disable()
            sec = io.StringIO()
            sortby = SortKey.CUMULATIVE
            pstat = pstats.Stats(prof, stream=sec).sort_stats(sortby)
            pstat.print_stats()
            print(sec.getvalue())

    def copy(self):
        """Creates a copy of this mesh."""
        # Caveat: this is a shallow copy!
        # In particular, we don't copy the __originalmesh (Mesh.Mesh)
        # So we point on the same object, which should not be modified
        new_mesh = copy.copy(self)
        # pylint: disable=protected-access, unused-private-member
        new_mesh.__transformation = copy.copy(self.transformation)
        return new_mesh

    def getPointNormals(self):  # pylint: disable=invalid-name
        """Get the normals for each point."""
        return self.__vnormals

    @property
    def transformation(self):
        """Get the mesh transformation."""
        return self.__transformation

    @property
    def points(self):
        """Get a collection of the mesh points."""
        return self.__points

    @property
    def count_points(self):
        """Get the number of points."""
        return len(self.__points)

    @property
    def facets(self):
        """Get a collection of the mesh facets."""
        return self.__facets

    @property
    def count_facets(self):
        """Get the number of facets."""
        return len(self.__facets)

    @property
    def autosmooth(self):
        """Get the smoothness state of the mesh (boolean)."""
        return self.__autosmooth

    def write_objfile(
        self,
        name,
        objfile=None,
        mtlfile=None,
        mtlname=None,
        mtlcontent=None,
        normals=True,
        uv_translate=(0.0, 0.0),
        uv_rotate=0.0,
        uv_scale=1.0,
    ):
        """Write an OBJ file from a mesh.

        Args:
            name -- Name of the mesh (str)
            objfile -- Name of the OBJ file (str). If None, the OBJ file is
              written in a temporary file, whose name is returned by the
              function.
            mtlfile -- MTL file name to reference in OBJ (optional) (str)
            mtlname -- Material name to reference in OBJ, must be defined in
              MTL file (optional) (str)
            mtlcontent -- MTL file content (optional) (str)
            normals -- Flag to control the writing of normals in the OBJ file
              (bool)
            uv_translate -- UV translation vector (2-uple)
            uv_rotate -- UV rotation angle in degrees (float)
            uv_scale -- UV scale factor (float)

        Returns: the name of file that the function wrote.
        """
        tm0 = time.time()
        if self.multiprocessing:
            func, mode = self._write_objfile_mp, "mp"
        else:
            func, mode = self._write_objfile_sp, "sp"

        # Create OBJ file (empty)
        if objfile is None:
            f_handle, objfile = tempfile.mkstemp(suffix=".obj", prefix="_")
            os.close(f_handle)
            del f_handle
        else:
            objfile = str(objfile)

        # Pack uv transformation
        uv_transformation = (uv_translate, uv_rotate, uv_scale)

        # Call main routine (single or multi process)
        objfile = func(
            name,
            objfile,
            mtlfile,
            mtlname,
            mtlcontent,
            normals,
            uv_transformation,
        )

        tm1 = time.time() - tm0
        App.Console.PrintLog(
            f"[Render][OBJ file] Write OBJ file ({mode}): {tm1}\n"
        )
        return objfile

    def _write_objfile_sp(
        self,
        name,
        objfile,
        mtlfile,
        mtlname,
        mtlcontent,
        normals,
        uv_transformation,
    ):
        """Write an OBJ file from a mesh - single process.

        See write_objfile for more details.
        """
        # Retrieve and normalize arguments
        normals = bool(normals)

        # Header
        header = ["# Written by FreeCAD-Render\n"]

        # Mtl
        if mtlcontent is not None:
            # Write mtl file
            mtlfilename = RenderMesh.write_mtl(mtlname, mtlcontent, mtlfile)
            if os.path.dirname(mtlfilename) != os.path.dirname(objfile):
                raise ValueError(
                    "OBJ and MTL files shoud be in the same dir\n"
                    f"('{objfile}' versus '{mtlfilename}')"
                )
            mtlfilename = os.path.basename(mtlfilename)
            mtl = [f"mtllib {mtlfilename}\n\n"]
        else:
            mtl = []

        # Vertices
        fmtv = functools.partial(str.format, "v {} {} {}\n")
        verts = (fmtv(*v) for v in self.points)
        verts = it.chain(["# Vertices\n"], verts, ["\n"])

        # UV
        if self.has_uvmap():
            # Translate, rotate, scale (optionally)
            uvs = self.uvtransform(*uv_transformation)
            fmtuv = functools.partial(str.format, "vt {} {}\n")
            uvs = (fmtuv(*t) for t in uvs)
            uvs = it.chain(["# Texture coordinates\n"], uvs, ["\n"])
        else:
            uvs = []

        # Vertex normals
        if self.has_vnormals():
            norms = self.__vnormals
            fmtn = functools.partial(str.format, "vn {} {} {}\n")
            norms = (fmtn(*n) for n in norms)
            norms = it.chain(["# Vertex normals\n"], norms, ["\n"])
        else:
            norms = []

        # Object name
        objname = [f"o {name}\n"]
        if mtlname is not None:
            objname.append(f"usemtl {mtlname}\n")
        objname.append("\n")

        # Faces
        if self.has_vnormals() and self.has_uvmap():
            mask = " {0}/{0}/{0}"
        elif not self.has_vnormals() and self.has_uvmap():
            mask = " {0}/{0}"
        elif self.has_vnormals() and not self.has_uvmap():
            mask = " {0}//{0}"
        else:
            mask = " {}"

        fmtf = functools.partial(str.format, mask)
        joinf = functools.partial(str.join, "")

        faces = (
            joinf(["f"] + [fmtf(x + 1) for x in f] + ["\n"])
            for f in self.facets
        )
        faces = it.chain(["# Faces\n"], faces)

        res = it.chain(header, mtl, verts, uvs, norms, objname, faces)

        with open(objfile, "w", encoding="utf-8") as f:
            f.writelines(res)

        return objfile

    def _write_objfile_mp(
        self,
        name,
        objfile,
        mtlfile,
        mtlname,
        mtlcontent,
        normals,
        uv_transformation,
    ):
        """Write an OBJ file from a mesh - multi process version.

        See write_objfile for more details.
        """
        # Retrieve and normalize arguments
        normals = bool(normals)

        # Initialize
        path = os.path.join(PKGDIR, "rendermesh_mp", "writeobj.py")

        # Header
        header = ["# Written by FreeCAD-Render\n"]

        # Mtl
        if mtlcontent is not None:
            # Write mtl file
            mtlfilename = RenderMesh.write_mtl(mtlname, mtlcontent, mtlfile)
            if os.path.dirname(mtlfilename) != os.path.dirname(objfile):
                raise ValueError(
                    "OBJ and MTL files shoud be in the same dir\n"
                    f"('{objfile}' versus '{mtlfilename}')"
                )
            mtlfilename = os.path.basename(mtlfilename)
            mtl = [f"mtllib {mtlfilename}\n\n"]
        else:
            mtl = []

        # UV
        if self.has_uvmap():
            # Translate, rotate, scale (optionally)
            uvs = self.uvtransform(*uv_transformation)
        else:
            uvs = []

        # Vertex normals
        if self.has_vnormals():
            norms = self.__vnormals
        else:
            norms = []

        # Object name
        objname = [f"o {name}\n"]
        if mtlname is not None:
            objname.append(f"usemtl {mtlname}\n")
        objname.append("\n")

        # Faces
        if self.has_vnormals() and self.has_uvmap():
            mask = " {0}/{0}/{0}"
        elif not self.has_vnormals() and self.has_uvmap():
            mask = " {0}/{0}"
        elif self.has_vnormals() and not self.has_uvmap():
            mask = " {0}//{0}"
        else:
            mask = " {}"

        inlist = [
            (header, "s"),
            (mtl, "s"),
            (self.points, "v"),
            (uvs, "vt"),
            (norms, "vn"),
            (objname, "s"),
            (self.facets, "f"),
        ]

        # Run
        runpy.run_path(
            path,
            init_globals={
                "inlist": inlist,
                "mask": mask,
                "objfile": objfile,
                "python": self.python,
            },
            run_name="__main__",
        )

        return objfile

    def uvtransform(self, translate, rotate, scale):
        """Compute a uv transformation (iterator).

        Args:
            uvmap -- the uv map to transform
            translate -- Translation vector (Vector2d)
            rotate -- Rotation angle in degrees (float)
            scale -- Scale factor (float)
        """
        uvmap = self.uvmap
        trans_x, trans_y = translate

        scale = float(scale)

        rotate = radians(float(rotate))

        def _000():
            """Nop."""
            return iter(uvmap)

        def _00t():
            """Translate."""
            return ((vec[0] + trans_x, vec[1] + trans_y) for vec in uvmap)

        def _0s0():
            """Scale."""
            return ((vec[0] * scale, vec[1] * scale) for vec in uvmap)

        def _0st():
            """Scale, translate."""
            return (
                (vec[0] * scale + trans_x, vec[1] * scale + trans_y)
                for vec in uvmap
            )

        def _r00():
            """Rotate."""
            cosr = cos(rotate)
            sinr = sin(rotate)
            return (
                (
                    vec[0] * cosr - vec[1] * sinr,
                    vec[0] * sinr + vec[1] * cosr,
                )
                for vec in uvmap
            )

        def _r0t():
            """Rotate, translate."""
            cosr = cos(rotate)
            sinr = sin(rotate)
            return (
                (
                    vec[0] * cosr - vec[1] * sinr + trans_x,
                    vec[0] * sinr + vec[1] * cosr + trans_y,
                )
                for vec in uvmap
            )

        def _rs0():
            """Rotate, scale."""
            cosrs = cos(rotate) * scale
            sinrs = sin(rotate) * scale
            return (
                (
                    vec[0] * cosrs - vec[1] * sinrs,
                    vec[0] * sinrs + vec[1] * cosrs,
                )
                for vec in uvmap
            )

        def _rst():
            """Rotate, scale, translate."""
            cosrs = cos(rotate) * scale
            sinrs = sin(rotate) * scale
            return (
                (
                    vec[0] * cosrs - vec[1] * sinrs + trans_x,
                    vec[0] * sinrs + vec[1] * cosrs + trans_y,
                )
                for vec in uvmap
            )

        # Select and return the right function
        index = (
            rotate != 0.0,
            scale != 1.0,
            trans_x != 0.0 or trans_y != 0.0,
        )
        index = sum(it.compress((4, 2, 1), index))
        functions = (_000, _00t, _0s0, _0st, _r00, _r0t, _rs0, _rst)
        return functions[index]()

    @staticmethod
    def write_mtl(name, mtlcontent, mtlfile=None):
        """Write a MTL file.

        MTL file is the companion of OBJ file, thus we keep this method in
        RenderMesh, although there is no need of 'self' to write the MTL...

        Args:
        name -- The material name, to be referenced in OBJ (str)
        mtlcontent -- The material content (str)
        mtlfile -- The mtl file name to write to. If None, a temp file is
          created. (str)

        Returns:
        The MTL file name
        """
        if mtlfile is None:
            f_handle, mtlfile = tempfile.mkstemp(suffix=".mtl", prefix="_")
            os.close(f_handle)

        # _write_material(name, material)
        with open(mtlfile, "w", encoding="utf-8") as f:
            f.write(f"newmtl {name}\n")
            f.write(mtlcontent)
        return mtlfile

    @property
    def uvmap(self):
        """Get mesh uv map."""
        return self.__uvmap

    def uvmap_per_vertex(self):
        """Get mesh uv map by vertex.

        (used in Cycles)
        """
        return [
            self.__uvmap[vertex_index]
            for triangle in self.__facets
            for vertex_index in triangle
        ]

    def center_of_gravity(self):
        """Get mesh's center of gravity.

        Mesh CoG is the barycenter of the facets CoG, weighted by facets
        areas
        """

        def reducer(partial, facet):
            """Reduce facets for center of gravity computation."""
            sum1, sum2 = partial
            points, area = facet
            weight = area / len(points)
            facetbar = (sum(x) * weight for x in zip(*points))
            sum1 = tuple(x + y for x, y in zip(sum1, facetbar))
            sum2 += area
            return sum1, sum2

        facets = ((f.Points, f.Area) for f in self.__originalmesh.Facets)
        sum1, sum2 = functools.reduce(reducer, facets, ((0.0, 0.0, 0.0), 0.0))
        cog = App.Vector(sum1) / sum2
        return cog

    def compute_uvmap(self, projection):
        """Compute UV map for this mesh."""
        # Warning:
        # The computation should ensure consistency on the following data:
        # - self.__points
        # - self.__facets
        # - self.__normals
        # - self.__areas
        projection = "Cubic" if projection is None else projection
        tm0 = time.time()
        if projection == "Cubic":
            self._compute_uvmap_cube()
        elif projection == "Spherical":
            self._compute_uvmap_sphere()
        elif projection == "Cylindric":
            self._compute_uvmap_cylinder()
        else:
            raise ValueError
        App.Console.PrintLog(f"[Render][Uvmap] Ending: {time.time() - tm0}\n")

    def _compute_uvmap_cylinder(self):
        """Compute UV map for cylindric case.

        Cylinder axis is supposed to be z.
        """
        # Split mesh into 3 submeshes:
        # non z-normal facets, not on seam (regular)
        # non z-normal facets, on seam (seam)
        # z-normal facets
        regular, seam, znormal = [], [], []
        z_vector = App.Base.Vector(0.0, 0.0, 1.0)
        for facet in self.__originalmesh.Facets:
            if _is_facet_normal_to_vector(facet, z_vector):
                znormal.append(facet)
            elif _facet_overlap_seam(facet):
                seam.append(facet)
            else:
                regular.append(facet)

        # Rebuild a complete mesh from submeshes, with uvmap
        mesh = Mesh.Mesh()
        uvmap = []

        # Non Z-normal facets (regular)
        regular_mesh = Mesh.Mesh(regular)
        points = list(regular_mesh.Points)
        avg_radius = sum(hypot(p.x, p.y) for p in points) / len(points)
        uvmap += [
            (atan2(p.x, p.y) * avg_radius * 0.001, p.z * 0.001) for p in points
        ]
        mesh.addMesh(regular_mesh)

        # Non Z-normal facets (seam)
        seam_mesh = Mesh.Mesh(seam)
        points = list(seam_mesh.Points)
        avg_radius = (
            sum(hypot(p.x, p.y) for p in points) / len(points) if points else 0
        )
        uvmap += [
            (_pos_atan2(p.x, p.y) * avg_radius * 0.001, p.z * 0.001)
            for p in points
        ]
        mesh.addMesh(seam_mesh)

        # Z-normal facets
        z_mesh = Mesh.Mesh(znormal)
        uvmap += [(p.x / 1000, p.y / 1000) for p in list(z_mesh.Points)]
        mesh.addMesh(z_mesh)

        # Replace previous values with newly computed ones
        points, facets = tuple(mesh.Topology)
        points = [tuple(p) for p in points]
        self.__points = points
        self.__facets = facets
        self.__normals = [tuple(f.Normal) for f in mesh.Facets]
        self.__areas = [f.Area for f in mesh.Facets]
        self.__uvmap = uvmap

    def _compute_uvmap_sphere(self):
        """Compute UV map for spherical case."""
        # Split mesh into 2 submeshes:
        # - facets not on seam (regular)
        # - facets on seam (seam)
        regular, seam = [], []
        for facet in self.__originalmesh.Facets:
            if _facet_overlap_seam(facet):
                seam.append(facet)
            else:
                regular.append(facet)

        # Rebuild a complete mesh from submeshes, with uvmap
        mesh = Mesh.Mesh()
        uvmap = []
        try:
            origin = self.__originalmesh.CenterOfGravity
        except AttributeError:
            origin = self.center_of_gravity()

        # Regular facets
        regular_mesh = Mesh.Mesh(regular)
        vectors = [p.Vector - origin for p in list(regular_mesh.Points)]
        uvmap += [
            (
                (0.5 + atan2(v.x, v.y) / (2 * pi)) * (v.Length / 1000.0 * pi),
                (0.5 + asin(v.z / v.Length) / pi) * (v.Length / 1000.0 * pi),
            )
            for v in vectors
        ]
        mesh.addMesh(regular_mesh)

        # Seam facets
        seam_mesh = Mesh.Mesh(seam)
        vectors = [p.Vector - origin for p in list(seam_mesh.Points)]
        uvmap += [
            (
                (0.5 + _pos_atan2(v.x, v.y) / (2 * pi))
                * (v.Length / 1000.0 * pi),
                (0.5 + asin(v.z / v.Length) / pi) * (v.Length / 1000.0 * pi),
            )
            for v in vectors
        ]
        mesh.addMesh(seam_mesh)

        # Replace previous values with newly computed ones
        points, facets = tuple(mesh.Topology)
        self.__points = [tuple(p) for p in points]
        self.__facets = facets
        self.__normals = [tuple(f.Normal) for f in mesh.Facets]
        self.__areas = [f.Area for f in mesh.Facets]
        self.__uvmap = uvmap

    def _compute_uvmap_cube(self):
        """Compute UV map for cubic case.

        We isolate submeshes by cube face in order to avoid trouble when
        one edge belongs to several cube faces (cf. simple cube case, for
        instance)
        """
        if self.multiprocessing:
            App.Console.PrintLog("[Render][Uvmap] Compute uvmap (mp)\n")
            func = self._compute_uvmap_cube_mp
        else:
            App.Console.PrintLog("[Render][Uvmap] Compute uvmap (sp)\n")
            func = self._compute_uvmap_cube_sp

        func()
        assert self.has_uvmap()

    def _compute_uvmap_cube_sp(self):
        """Compute UV map for cubic case - single process version.

        We isolate submeshes by cube face in order to avoid trouble when
        one edge belongs to several cube faces (cf. simple cube case, for
        instance)
        """
        # Isolate submeshes by cube face
        face_facets = ([], [], [], [], [], [])
        for facet in self.__originalmesh.Facets:
            cubeface = _intersect_unitcube_face(facet.Normal)
            # Add facet to corresponding submesh
            face_facets[cubeface].append(facet)

        # Rebuid a complete mesh from face submeshes, with uvmap
        uvmap = []
        mesh = Mesh.Mesh()
        try:
            cog = self.__originalmesh.CenterOfGravity
        except AttributeError:
            cog = self.center_of_gravity()
        for cubeface, facets in enumerate(face_facets):
            facemesh = Mesh.Mesh(facets)
            # Compute uvmap of the submesh
            facemesh_uvmap = [
                _compute_uv_from_unitcube((p.Vector - cog) / 1000, cubeface)
                # pylint: disable=not-an-iterable
                for p in facemesh.Points
            ]
            # Add submesh and uvmap
            mesh.addMesh(facemesh)
            uvmap += facemesh_uvmap

        # Replace previous values with newly computed ones
        points, facets = tuple(mesh.Topology)
        points = [tuple(p) for p in points]
        self.__points = points
        self.__facets = facets
        self.__normals = [tuple(f.Normal) for f in mesh.Facets]
        self.__areas = [f.Area for f in mesh.Facets]
        self.__uvmap = uvmap

    def _compute_uvmap_cube_mp(self):
        """Compute UV map for cubic case - multiprocessing version.

        We isolate submeshes by cube face in order to avoid trouble when
        one edge belongs to several cube faces (cf. simple cube case, for
        instance)
        """
        # Init variables
        path = os.path.join(PKGDIR, "rendermesh_mp", "uvmap_cube.py")

        # Run
        res = runpy.run_path(
            path,
            init_globals={
                "POINTS": self.__points,
                "FACETS": self.__facets,
                "NORMALS": self.__normals,
                "AREAS": self.__areas,
                "UVMAP": self.__uvmap,
                "PYTHON": self.python,
                "SHOWTIME": self.debug,
            },
            run_name="__main__",
        )
        self.__points = res["POINTS"]
        self.__facets = res["FACETS"]
        self.__uvmap = res["UVMAP"]

        # Clean
        del res["POINTS"]
        del res["FACETS"]
        del res["NORMALS"]
        del res["AREAS"]
        del res["UVMAP"]
        del res["PYTHON"]
        del res["SHOWTIME"]

    def has_uvmap(self):
        """Check if object has a uv map."""
        return bool(self.__uvmap)

    def compute_vnormals(self):
        """Compute vertex normals.

        Refresh self._normals. We use an area & angle weighting algorithm."
        """
        # See here
        # http://www.bytehazard.com/articles/wnormals.html
        # (and look at script wnormals100.ms)

        # TODO Optimize
        fmul = vector3d.fmul
        v3d_angles = vector3d.angles
        add = vector3d.add
        safe_normalize = vector3d.safe_normalize
        points = self.__points
        normals = self.__normals
        areas = self.__areas

        vnorms = [(0, 0, 0)] * self.count_points
        for index, facet in enumerate(self.__facets):
            normal = normals[index]
            area = areas[index]
            angles = v3d_angles(points[i] for i in facet)
            for point_index, angle in zip(facet, angles):
                weighted_vnorm = fmul(normal, angle * area)
                vnorms[point_index] = add(vnorms[point_index], weighted_vnorm)

        # Normalize
        vnorms = [safe_normalize(n) for n in vnorms]

        self.__vnormals = vnorms

    # TODO Remove
    def compute_vnormals_old(self):
        """Compute vertex normals.

        Refresh self._normals. We use an area & angle weighting algorithm."
        """
        # See here
        # http://www.bytehazard.com/articles/wnormals.html
        # (and look at script wnormals100.ms)

        # TODO Optimize

        vnorms = [(0, 0, 0)] * self.count_points
        for facet in self.__facets:
            triangle = [self.__points[i] for i in facet]
            weighted_vnorm = vector3d.normal(triangle)
            angles = vector3d.angles(triangle)
            for point_index, angle in zip(facet, angles):
                weighted_vnorm = vector3d.fmul(
                    weighted_vnorm, angle
                )  # Weight with angle
                vnorms[point_index] = vector3d.add(
                    vnorms[point_index], weighted_vnorm
                )

        # Normalize
        vnorms = [vector3d.safe_normalize(n) for n in vnorms]

        self.__vnormals = vnorms

    def has_vnormals(self):
        """Check if object has a normals."""
        return bool(self.__vnormals)

    def connected_facets(
        self,
        starting_facet_index,
        adjacents,
        tags,
        new_tag,
        split_angle_cos,
    ):
        """Get the maximal connected component containing the starting facet.

        It uses a depth-first search algorithm, iterative version.
        Caveat:
        - tags may be modified by the algorithm.

        Args:
            starting_facet_index -- the index of the facet to start
                with (integer)
            adjacents -- adjacency lists (one list per facet)
            tags -- the tags that have already been set (list, same size as
                self.__facets)
            new_tag -- the tag to use to mark the component
            split_angle_cos -- the cos of the angle that breaks adjacency

        Returns:
            A list of tags (same size as self.__facets). The elements tagged
            with 'new_tag' are the computed connected component.
        """
        # Init
        split_angle_cos = float(split_angle_cos)
        dot = vector3d.dot
        normals = self.__normals

        # Create and init stack
        stack = [starting_facet_index]

        # Tag starting facet
        tags[starting_facet_index] = new_tag

        while stack:
            # Current index (stack top)
            current_index = stack[-1]
            current_normal = normals[current_index]

            # Forward
            while adjacents[current_index]:
                successor_index = adjacents[current_index].pop()

                # Test angle
                try:
                    successor_normal = normals[successor_index]
                except IndexError:
                    # Facet.NeighbourIndices can contain irrelevant index...
                    continue

                if dot(current_normal, successor_normal) < split_angle_cos:
                    continue

                if tags[successor_index] is None:
                    # successor is not tagged, we can go on forward
                    tags[successor_index] = new_tag
                    stack.append(successor_index)
                    current_index = successor_index
                    current_normal = normals[current_index]

            # Backward
            successor_index = stack.pop()

        # Final
        return tags

    def adjacent_facets(self, split_angle_cos="-inf"):
        """Compute the adjacent facets for each facet of the mesh.
        Returns a list of sets of facet indices.
        """
        # For each point, compute facets that contain this point as a vertex
        iterator = (
            (facet_index, point_index)
            for facet_index, facet in enumerate(self.__facets)
            for point_index in facet
        )

        def fpp_reducer(rolling, new):
            facet_index, point_index = new
            facets_per_point[point_index].append(facet_index)

        facets_per_point = [list() for _ in range(self.count_points)]
        functools.reduce(fpp_reducer, iterator, None)

        # Compute adjacency
        normals = self.__normals
        facets = [set(f) for f in self.__facets]
        iterator = (
            (facet_idx, other_idx)
            for facet_idx, facet in enumerate(facets)
            for point_idx in facet
            for other_idx in facets_per_point[point_idx]
            if len(facet & facets[other_idx]) == 2
        )

        adjacents = [set() for _ in range(self.count_facets)]
        def reduce_adj(rolling, new):
            facet_index, other_index = new
            adjacents[facet_index].add(other_index)

        functools.reduce(reduce_adj, iterator, None)

        return adjacents


    def connected_components(self, split_angle=radians(30)):
        """Get all connected components of facets in the mesh.

        Args:
            split_angle -- the angle that breaks adjacency

        Returns:
            a list of tags. Each tag gives the component of the corresponding
                facet
            the number of components
        """
        split_angle_cos = cos(split_angle)

        # TODO
        # adjacents = [
            # list(f.NeighbourIndices) for f in self.__originalmesh.Facets
        # ]
        adjacents = self.adjacent_facets()

        tags = [None] * self.count_facets
        tag = None

        iterator = zip(
            it.count(), (x for x, y in enumerate(tags) if y is None)
        )
        for tag, starting_point in iterator:
            tags = self.connected_facets(
                starting_point, adjacents, tags, tag, split_angle_cos
            )

        return tags, tag

    def separate_connected_components(self, split_angle=radians(30)):
        """Operate a separation into the mesh between connected components.

        Only points are modified. Facets are kept as-is.

        Args:
            split_angle -- angle threshold, above which 2 adjacents facets
                are considered as non-connected (in radians)
        """
        tags, _ = self.connected_components(split_angle)

        points = self.__points
        facets = self.__facets

        # Initialize the map
        newpoints = {
            (point_index, tag): None
            for facet, tag in zip(facets, tags)
            for point_index in facet
        }

        # Number newpoint
        for index, point in enumerate(newpoints):
            newpoints[point] = index

        # Rebuild point list
        self.__points = [points[point_index] for point_index, tag in newpoints]

        # If necessary, rebuild uvmap
        if self.__uvmap:
            self.__uvmap = [
                self.__uvmap[point_index]
                for point_index, tag in newpoints
            ]

        # Update point indices in facets
        self.__facets = [
            tuple(newpoints[point_index, tag] for point_index in facet)
            for facet, tag in zip(facets, tags)
        ]


# ===========================================================================
#                               RenderTransformation
# ===========================================================================


class _Transformation:
    """A extension of Placement, implementing also scale."""

    def __init__(self, placement=App.Placement(), scale=1.0):
        """Initialize transformation."""
        self.__placement = App.Placement(placement)
        self.__scale = float(scale)

    def apply_placement(self, placement, left=False):
        """Apply a FreeCAD placement to this.

        By default, placement is applied on the right, but it can also be
        applied on the left, with 'left' parameter.

        """
        placement = App.Placement(placement)
        if not left:
            self.__placement *= placement
        else:
            placement *= self.__placement
            self.__placement = placement

    def __str__(self):
        """Give a string representation."""
        return f"Placement={self.__placement}, Scale={self.__scale}"

    @property
    def scale(self):
        """Get scale property."""
        return self.__scale

    @scale.setter
    def scale(self, new_scale):
        """Set scale property."""
        new_scale = float(new_scale)
        assert new_scale, "new_scale cannot be zero"
        self.__scale = new_scale

    # Getters
    def get_matrix_fcd(self):
        """Get transformation matrix in FreeCAD format."""
        mat = App.Matrix(self.__placement.toMatrix())

        # Scale
        scale = self.__scale
        mat.scale(self.__scale)
        mat.A41 *= scale
        mat.A42 *= scale
        mat.A43 *= scale

        return mat

    def get_matrix_rows(self):
        """Get transformation matrix as a list of rows."""
        mat = self.__placement.Matrix

        # Get plain transfo
        transfo_rows = [mat.A[i * 4 : (i + 1) * 4] for i in range(4)]

        # Apply scale
        transfo_rows = [
            [val * self.__scale if rownumber < 3 else val for val in row]
            for rownumber, row in enumerate(transfo_rows)
        ]
        return transfo_rows

    def get_matrix_columns(self):
        """Get transformation matrix as a list of columns."""
        transfo_rows = self.get_matrix_rows()
        transfo_cols = list(zip(*transfo_rows))
        return transfo_cols

    def get_translation(self):
        """Get translation component."""
        scale = self.__scale
        return tuple(v * scale for v in tuple(self.__placement.Base))

    def get_rotation_qtn(self):
        """Get rotation component as a quaternion."""
        return tuple(self.__placement.Rotation.Q)

    def get_rotation_ypr(self):
        """Get rotation component as yaw-pitch-roll angles."""
        try:
            # >0.20
            return self.__placement.Rotation.getYawPitchRoll()
        except AttributeError:
            # 0.19
            return self.__placement.Rotation.toEuler()

    def get_scale(self):
        """Get scale component as single scalar."""
        return self.__scale

    def get_scale_vector(self):
        """Get scale component as vector."""
        scale = self.__scale
        return (scale, scale, scale)


# ===========================================================================
#                           Cube uvmap helpers
# ===========================================================================


class _UnitCubeFaceEnum(enum.IntEnum):
    """A class to describe a face of a unit cube.

    A unit cube is cube centered on the origin, each face perpendicular to one
    of the axes of the reference frame and the distance from the origin to each
    face being equal to 1.
    This cube is useful for projections for uv map...
    """

    XPLUS = 0
    XMINUS = 1
    YPLUS = 2
    YMINUS = 3
    ZPLUS = 4
    ZMINUS = 5


# Normals of the faces of the unit cube
_UNIT_CUBE_FACES_NORMALS = {
    _UnitCubeFaceEnum.XPLUS: (1.0, 0.0, 0.0),
    _UnitCubeFaceEnum.XMINUS: (-1.0, 0.0, 0.0),
    _UnitCubeFaceEnum.YPLUS: (0.0, 1.0, 0.0),
    _UnitCubeFaceEnum.YMINUS: (0.0, -1.0, 0.0),
    _UnitCubeFaceEnum.ZPLUS: (0.0, 0.0, 1.0),
    _UnitCubeFaceEnum.ZMINUS: (0.0, 0.0, -1.0),
}


def _intersect_unitcube_face(direction):
    """Get the face of the unit cube intersected by a line from origin.

    Args:
        direction -- The directing vector for the intersection line
        (a 3-float sequence)

    Returns:
        A face from the unit cube (_UnitCubeFaceEnum)
    """
    dirx, diry, dirz = direction
    dabsx, dabsy, dabsz = abs(dirx), abs(diry), abs(dirz)

    if dabsx >= dabsy and dabsx >= dabsz:
        return (
            0  # _UnitCubeFaceEnum.XPLUS
            if dirx >= 0
            else 1  # _UnitCubeFaceEnum.XMINUS
        )

    if dabsy >= dabsx and dabsy >= dabsz:
        return (
            2  # _UnitCubeFaceEnum.YPLUS
            if diry >= 0
            else 3  # _UnitCubeFaceEnum.YMINUS
        )

    return (
        4  # _UnitCubeFaceEnum.ZPLUS
        if dirz >= 0
        else 5  # _UnitCubeFaceEnum.ZMINUS
    )


def _uc_xplus(point):
    """Unit cube - xplus case."""
    _, pt1, pt2 = point
    return (pt1, pt2)


def _uc_xminus(point):
    """Unit cube - xminus case."""
    _, pt1, pt2 = point
    return (-pt1, pt2)


def _uc_yplus(point):
    """Unit cube - yplus case."""
    pt0, _, pt2 = point
    return (-pt0, pt2)


def _uc_yminus(point):
    """Unit cube - yminus case."""
    pt0, _, pt2 = point
    return (pt0, pt2)


def _uc_zplus(point):
    """Unit cube - zplus case."""
    pt0, pt1, _ = point
    return (pt0, pt1)


def _uc_zminus(point):
    """Unit cube - zminus case."""
    pt0, pt1, _ = point
    return (pt0, -pt1)


_UC_MAP = (
    _uc_xplus,
    _uc_xminus,
    _uc_yplus,
    _uc_yminus,
    _uc_zplus,
    _uc_zminus,
)


def _compute_uv_from_unitcube(point, face):
    """Compute UV coords from intersection point and face.

    The cube is unfold this way:

          +Z
    +X +Y -X -Y
          -Z

    """
    # pt0, pt1, pt2 = point
    # if face == 0:  # _UnitCubeFaceEnum.XPLUS
    # res = (pt1, pt2)
    # elif face == 1:  # _UnitCubeFaceEnum.XMINUS
    # res = (-pt1, pt2)
    # elif face == 2:  # _UnitCubeFaceEnum.YPLUS
    # res = (-pt0, pt2)
    # elif face == 3:  # _UnitCubeFaceEnum.YMINUS
    # res = (pt0, pt2)
    # elif face == 4:  # _UnitCubeFaceEnum.ZPLUS
    # res = (pt0, pt1)
    # elif face == 5:  # _UnitCubeFaceEnum.ZMINUS
    # res = (pt0, -pt1)
    method = _UC_MAP[face]
    return method(point)


# ===========================================================================
#                           Other uvmap helpers
# ===========================================================================

def _safe_normalize(vec):
    """Safely normalize a FreeCAD Vector.

    If vector's length is 0, returns (0, 0, 0).
    """
    try:
        res = vec.normalize()
    except App.Base.FreeCADError:
        res = App.Base.Vector(0.0, 0.0, 0.0)
    return res

def _is_facet_normal_to_vector(facet, vector):
    """Test whether a facet is normal to a vector.

    math.isclose is used to assess dot product nullity.
    """
    pt1, pt2, pt3 = facet.Points
    vec1 = _safe_normalize(App.Base.Vector(*pt2) - App.Base.Vector(*pt1))
    vec2 = _safe_normalize(App.Base.Vector(*pt3) - App.Base.Vector(*pt1))
    vector = _safe_normalize(vector)
    tolerance = 1e-5
    res = isclose(vec1.dot(vector), 0.0, abs_tol=tolerance) and isclose(
        vec2.dot(vector), 0.0, abs_tol=tolerance
    )
    return res


def _facet_overlap_seam(facet):
    """Test whether facet overlaps the seam."""
    phis = [atan2(x, y) for x, y, _ in facet.Points]
    return max(phis) * min(phis) < 0


def _pos_atan2(p_x, p_y):
    """Wrap atan2 to get only positive values (seam treatment)."""
    atan2_xy = atan2(p_x, p_y)
    return atan2_xy if atan2_xy >= 0 else atan2_xy + 2 * pi


def _find_python():
    """Find Python executable."""
    python = shutil.which("pythonw")
    if python:
        python = os.path.abspath(python)
        return python

    python = shutil.which("python")
    if python:
        python = os.path.abspath(python)
        return python

    return None