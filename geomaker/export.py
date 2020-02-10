import ctypes as ct
import hashlib
from pathlib import Path

from matplotlib import cm as colormap
import numpy as np
from PIL import Image
from splipy import Surface, BSplineBasis
from splipy.io import G2
from stl.mesh import Mesh as STLMesh

from .asynchronous import async_job


def list_colormaps():
    return colormap.cmap_d.keys()


def array_to_image(data, fmt, cmap, filename):
    maxval = np.max(data)
    if maxval > 0:
        data /= np.max(data)
    data = getattr(colormap, cmap)(data, bytes=True)
    if data.shape[-1] == 4 and fmt == 'jpeg':
        data = data[..., :3]
    image = Image.fromarray(data)
    image.save(str(filename), format=fmt.upper())


def is_image_format(fmt):
    return fmt in {'png', 'jpeg'}


@async_job()
def export_job(*args, **kwargs):
    return export(*args, **kwargs)


def export(polygon, project, manager, boundary_mode='exterior',
           rotation_mode='none', coords='utm33n', resolution=None,
           maxpts=None, format='png', colormap='terrain',
           zero_sea_level=True, filename=None, directory=None):

    manager.report_max(3)

    image_mode = is_image_format(format)
    if not image_mode:
        boundary_mode = 'actual'
        rotation_mode = 'none'

    manager.report_message('Generating geometry')
    if format == 'stl':
        x, y, tri = polygon.generate_triangulation(coords, resolution)
    else:
        x, y = polygon.generate_meshgrid(
            boundary_mode, rotation_mode, coords,
            resolution=resolution, maxpts=maxpts
        )
    manager.increment_progress()

    manager.report_message('Generating data')
    if image_mode:
        data = polygon.interpolate(project, x, y)
    else:
        data = polygon.interpolate(project, y, x)
    if not zero_sea_level:
        data -= np.min(data)
    manager.increment_progress()

    manager.report_message('Saving file')
    if filename is None:
        filename = hashlib.sha256(data.data).hexdigest() + '.' + format
        filename = Path(directory) / filename

    if image_mode:
        array_to_image(data, format, colormap, filename)
    elif format == 'g2':
        cpts = np.stack([x, y, data], axis=2)
        knots = [[0.0] + list(map(float, range(n))) + [float(n-1)] for n in data.shape]
        bases = [BSplineBasis(order=2, knots=kts) for kts in knots]
        srf = Surface(*bases, cpts, raw=True)
        with G2(filename) as g2:
            g2.write(srf)
    elif format == 'stl':
        mesh = STLMesh(np.zeros(tri.shape[0], STLMesh.dtype))
        mesh.vectors[:,:,0] = x[tri]
        mesh.vectors[:,:,1] = y[tri]
        mesh.vectors[:,:,2] = data[tri]
        mesh.save(filename)
    manager.increment_progress()

    return filename


class TriangulateIO(ct.Structure):
    _fields_ = [
        ('pointlist', ct.POINTER(ct.c_double)),
        ('pointattributelist', ct.POINTER(ct.c_double)),
        ('pointmarkerlist', ct.POINTER(ct.c_int)),
        ('numberofpoints', ct.c_int),
        ('numberofpointattributes', ct.c_int),
        ('trianglelist', ct.POINTER(ct.c_int)),
        ('triangleattributelist', ct.POINTER(ct.c_double)),
        ('trianglearealist', ct.POINTER(ct.c_double)),
        ('neighborlist', ct.POINTER(ct.c_int)),
        ('numberoftriangles', ct.c_int),
        ('numberofcorners', ct.c_int),
        ('numberoftriangleattributes', ct.c_int),
        ('segmentlist', ct.POINTER(ct.c_int)),
        ('segmentmarkerlist', ct.POINTER(ct.c_int)),
        ('numberofsegments', ct.c_int),
        ('holelist', ct.POINTER(ct.c_double)),
        ('numberofholes', ct.c_int),
        ('regionlist', ct.POINTER(ct.c_double)),
        ('numberofregions', ct.c_int),
        ('edgelist', ct.POINTER(ct.c_int)),
        ('edgemarkerlist', ct.POINTER(ct.c_int)),
        ('normlist', ct.POINTER(ct.c_double)),
        ('numberofedges', ct.c_int),
    ]


def triangulate(points, segments, max_area=None, verbose=False, library='libtriangle-1.6.so'):
    lib = ct.cdll.LoadLibrary(library)
    triangulate = lib.triangulate
    triangulate.argtypes = [
        ct.c_char_p,
        ct.POINTER(TriangulateIO),
        ct.POINTER(TriangulateIO),
        ct.POINTER(TriangulateIO),
    ]

    free = lib.trifree
    free.argtypes = [ct.c_void_p]

    inpoints = (ct.c_double * points.size)()
    inpoints[:] = list(points.flat)

    insegments = (ct.c_int * segments.size)()
    insegments[:] = list(segments.flat)

    inmesh = TriangulateIO(
        inpoints, None, None, len(points), 0,
        None, None, None, None, 0, 3, 0,
        insegments, None, len(segments),
        None, 0, None, 0, None, None, None, 0
    )

    outmesh = TriangulateIO()

    options = 'pzjq'
    if verbose:
        options += 'VV'
    else:
        options += 'Q'
    if max_area:
        options += f'a{max_area}'

    triangulate(options.encode(), ct.byref(inmesh), ct.byref(outmesh), None)

    npts = outmesh.numberofpoints
    outpoints = np.zeros((npts, 2))
    outpoints.flat[:] = outmesh.pointlist[:npts*2]

    nelems = outmesh.numberoftriangles
    outelements = np.zeros((nelems, 3), dtype=int)
    outelements.flat[:] = outmesh.trianglelist[:nelems*3]

    free(outmesh.pointlist)
    free(outmesh.pointattributelist)
    free(outmesh.pointmarkerlist)
    free(outmesh.trianglelist)
    free(outmesh.triangleattributelist)
    free(outmesh.trianglearealist)
    free(outmesh.neighborlist)
    free(outmesh.segmentlist)
    free(outmesh.segmentmarkerlist)
    free(outmesh.holelist)
    free(outmesh.regionlist)
    free(outmesh.edgelist)
    free(outmesh.edgemarkerlist)
    free(outmesh.normlist)

    return outpoints, outelements
