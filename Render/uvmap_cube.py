# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022 Howetuft <howetuft@gmail.com>                      *
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
import multiprocessing as mp




def compute_submeshes(normals):
    def intersect_unitcube_face(direction):
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

    return [intersect_unitcube_face(n) for n in normals]


def compute_uv(chunk):
    def compute_uv_from_unitcube(point, face):
        """Compute UV coords from intersection point and face.

        The cube is unfold this way:

              +Z
        +X +Y -X -Y
              -Z

        """
        point = tuple((p - c) / 1000 for p, c in zip(point, COG))
        pt0, pt1, pt2 = point
        if face == 0:  # _UnitCubeFaceEnum.XPLUS
            res = (pt1, pt2)
        elif face == 1:  # _UnitCubeFaceEnum.XMINUS
            res = (-pt1, pt2)
        elif face == 2:  # _UnitCubeFaceEnum.YPLUS
            res = (-pt0, pt2)
        elif face == 3:  # _UnitCubeFaceEnum.YMINUS
            res = (pt0, pt2)
        elif face == 4:  # _UnitCubeFaceEnum.ZPLUS
            res = (pt0, pt1)
        elif face == 5:  # _UnitCubeFaceEnum.ZMINUS
            res = (pt0, -pt1)
        return res
    return [compute_uv_from_unitcube(point, face) for point, face in chunk]

def init(*args):
    global COG
    COG, *_ = args

if __name__ == "__main__":
    import os
    import shutil
    import operator
    import itertools
    import time
    import functools

    import Mesh

    # Get variables
    # pylint: disable=used-before-assignment
    try:
        facets
    except NameError:
        facets = []

    try:
        cog
    except NameError:
        cog = (0.0, 0.0, 0.0)

    try:
        transmat
    except NameError:
        transmat = None

    # TODO Only >= 3.8
    def batched(iterable, n):
        "Batch data into lists of length n. The last batch may be shorter."
        # batched('ABCDEFG', 3) --> ABC DEF G
        # from Python itertools documentation...
        if n < 1:
            raise ValueError("n must be at least one")
        it = iter(iterable)
        while batch := list(itertools.islice(it, n)):
            yield batch

    # Set directory and stdout
    save_dir = os.getcwd()
    os.chdir(os.path.dirname(__file__))

    # Set executable
    executable = shutil.which("python")
    if not executable:
        raise RuntimeError("No Python executable")
    mp.set_executable(executable)
    mp.set_start_method("spawn", force=True)

    CHUNK_SIZE = 20000
    NPROC = os.cpu_count()

    # Run
    try:
        tm0 = time.time()
        chunks = batched((tuple(f.Normal) for f in facets), CHUNK_SIZE)
        with mp.Pool(NPROC, init, (cog,)) as pool:
            # Compute submeshes
            data = pool.imap(compute_submeshes, chunks)
            faces = (
                (ichunk * CHUNK_SIZE + iface, face)
                for ichunk, chunk in enumerate(data)
                for iface, face in enumerate(chunk)
            )

            def face_reducer(x, y):
                iface, face = y
                x[face].append(facets[iface])
                return x
            face_facets = functools.reduce(face_reducer, faces, [[], [], [], [], [], []])
            submeshes = [Mesh.Mesh(facets) for facets in face_facets]
            print("submeshes", time.time() - tm0)

            # Compute uvmap for submeshes
            points = (
                (tuple(p.Vector), cubeface)
                for cubeface, mesh in enumerate(submeshes)
                for p in mesh.Points
            )
            chunks = batched(points, CHUNK_SIZE)
            uvmap = sum(pool.imap(compute_uv, chunks), [])
            print("uv", time.time() - tm0)

            # Compute final mesh
            def mesh_reducer(x, y):
                y.transform(transmat)
                x.addMesh(y)
                return x
            mesh = functools.reduce(mesh_reducer, submeshes, Mesh.Mesh())
            print("mesh", time.time() - tm0)

            # Clean
            del face_facets
            del data
            del submeshes
    finally:
        os.chdir(save_dir)
