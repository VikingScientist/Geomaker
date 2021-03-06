from contextlib import contextmanager
from enum import IntEnum
from functools import lru_cache
import hashlib
from io import BytesIO
import json
from math import ceil
from operator import methodcaller
from pathlib import Path
import tempfile
from zipfile import ZipFile

from area import area as geojson_area
from matplotlib import cm as colormap
import numpy as np
from osgeo import gdal
from PIL import Image
import requests
import toml
from utm import from_latlon
from xdg import XDG_DATA_HOME, XDG_CONFIG_HOME


from bidict import bidict


import sqlalchemy as sql
import sqlalchemy.orm as orm
from sqlalchemy.ext.declarative import declarative_base

DeclarativeBase = declarative_base()


# Create database if it does not exist
DATA_ROOT = XDG_DATA_HOME / 'geomaker'
THUMBNAIL_ROOT = DATA_ROOT / 'thumbnails'

for d in [DATA_ROOT, THUMBNAIL_ROOT]:
    if not d.exists():
        d.mkdir()

# Config file
CONFIG_FILE = XDG_CONFIG_HOME / 'geomaker.toml'

# Projects
PROJECTS = [
    ('DTM50', 'Terrain model (50 m)'),
    ('DTM10', 'Terrain model (10 m)'),
    ('DTM1',  'Terrain model (1 m)'),
    ('DOM50', 'Object model (50 m)'),
    ('DOM10', 'Object model (10 m)'),
    ('DOM1',  'Object model (1 m)'),
]

for project, _ in PROJECTS:
    proj_dir = DATA_ROOT / project
    if not proj_dir.exists():
        proj_dir.mkdir()


def make_request(endpoint, params):
    params = json.dumps(params)
    url = f'https://hoydedata.no/laserservices/rest/{endpoint}.ashx?request={params}'
    response = requests.get(url)
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, json.loads(response.text)


class Config(dict):

    def __init__(self):
        super().__init__()
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                self.update(toml.load(f))

    def verify(self, querier):
        if not 'email' in self:
            querier.message(
                'E-mail address',
                'An e-mail address must be configured to make requests to the Norwegian Mapping Authority',
            )
            self['email'] = querier.query_str('E-mail address', 'E-mail:')
        self.write()

    def write(self):
        with open(CONFIG_FILE, 'w') as f:
            toml.dump(self, f)


class Polygon(DeclarativeBase):
    __tablename__ = 'polygon'

    id = sql.Column(sql.Integer, primary_key=True)
    name = sql.Column(sql.String, nullable=False)
    thumbnail = sql.Column(sql.String, nullable=True)

    points = orm.relationship(
        'Point', order_by='Point.id', back_populates='polygon', lazy='immediate',
        cascade='save-update, merge, delete, delete-orphan',
    )
    thumbnails = orm.relationship(
        'Thumbnail', back_populates='polygon', lazy='immediate',
        cascade='save-update, merge, delete, delete-orphan',
    )
    assocs = orm.relationship(
        'PolyTIFF', back_populates='polygon', lazy='immediate',
        cascade='save-update, merge, delete, delete-orphan'
    )
    jobs = orm.relationship(
        'Job', order_by='Job.jobid', back_populates='polygon', lazy='immediate',
        cascade='save-update, merge, delete, delete-orphan'
    )

    @property
    def lfid(self):
        return db.lfid.inverse.get(self.id, None)

    @lfid.setter
    def lfid(self, value):
        db.update_lfid(self, value)

    @property
    def geometry(self):
        for p in self.points:
            yield [p.x, p.y]

    @property
    def west(self):
        return min(x for x,_ in self.geometry)

    @property
    def east(self):
        return max(x for x,_ in self.geometry)

    @property
    def south(self):
        return min(y for _,y in self.geometry)

    @property
    def north(self):
        return max(y for _,y in self.geometry)

    @property
    def area(self):
        return geojson_area({'type': 'Polygon', 'coordinates': [list(self.geometry)]})

    @contextmanager
    def _assoc_query(self, cls, project, dedicated=None):
        filters = [cls.polygon == self, cls.project == project]
        if dedicated is not None:
            filters.append(cls.dedicated == dedicated)
        with db.session() as s:
            yield s.query(cls).filter(*filters)

    def _single_assoc(self, cls, project, dedicated=None):
        with self._assoc_query(cls, project, dedicated=dedicated) as q:
            return q.one_or_none()

    def thumbnail(self, project):
        return self._single_assoc(Thumbnail, project)

    def dedicated(self, project):
        obj = self._single_assoc(PolyTIFF, project, True)
        if obj:
            obj = obj.geotiff
        return obj

    def ntiles(self, project):
        with self._assoc_query(PolyTIFF, project, False) as q:
            return q.count()

    def tiles(self, project):
        with self._assoc_query(PolyTIFF, project, False) as q:
            for assoc in q:
                yield assoc.geotiff

    def delete_dedicated(self, project):
        db.delete_if(self.dedicated(project))

    def delete_tiles(self, project):
        with self._assoc_query(PolyTIFF, project, dedicated=False) as q:
            q.delete()

    def job(self, project, dedicated):
        return self._single_assoc(Job, project, dedicated)

    def delete_job(self, project, dedicated):
        obj = self.job(project, dedicated)
        db.delete_if(obj)

    def create_job(self, project, dedicated, email):
        if dedicated:
            assert self.dedicated(project) is None
        else:
            assert self.ntiles(project) == 0
        assert self.job(project, dedicated) is None

        coords = [pt.z33n for pt in self.points]
        coords = [(int(x), int(y)) for x, y in coords]
        coords = ';'.join(f'{x},{y}' for x, y in coords)
        params = {
            'CopyEmail': email,
            'Projects': project,
            'CoordInput': coords,
            'ProjectMerge': 1 if dedicated else 0,
            'InputWkid': 25833,      # ETRS89 / UTM zone 33N
            'Format': 5,             # GeoTIFF,
            'NHM': 1,                # National altitude models
        }

        code, response = make_request('startExport', params)
        if response is None:
            return f'HTTP code {code}'
        elif 'Error'in response:
            return response['Error']
        elif not response.get('Success', False):
            return 'Unknown error'

        job = Job(polygon=self, project=project, dedicated=dedicated, jobid=response['JobID'])
        with db.session() as s:
            s.add(job)

    def update_thumbnail(self, project, dedicated):
        if self.thumbnail(project) is not None and not dedicated:
            return
        db.delete_if(self.thumbnail(project))
        if self.dedicated(project):
            tiffs = [self.dedicated(project)]
        else:
            tiffs = list(self.tiles(project))

        # Crop image to actual region
        coords = [pt.z33n for pt in self.points]
        east = min(x for x,_ in coords)
        west = max(x for x,_ in coords)
        south = min(y for _,y in coords)
        north = max(y for _,y in coords)

        # Compute a suitable resolution
        res = max(north - south, east - west) / 640

        nx = int((north - south) // res)
        ny = int((west - east) // res)
        x, y = np.meshgrid(np.linspace(north, south, nx), np.linspace(east, west, ny), indexing='ij')

        image = np.zeros((nx, ny))
        for tiff in tiffs:
            tiff.interpolate(image, x, y)

        image /= np.max(image)
        image = colormap.terrain(image, bytes=True)
        filename = THUMBNAIL_ROOT / (hashlib.sha256(image.data).hexdigest() + '.png')

        image = Image.fromarray(image)
        image.save(filename)

        thumb = Thumbnail(filename=str(filename), project=project, polygon=self)
        with db.session() as s:
            s.add(thumb)


class Thumbnail(DeclarativeBase):
    __tablename__ = 'thumbnail'

    id = sql.Column(sql.Integer, primary_key=True)
    filename = sql.Column(sql.String, nullable=False)
    project = sql.Column(sql.String, nullable=False)
    polygon_id = sql.Column(sql.Integer, sql.ForeignKey('polygon.id'), nullable=False)
    polygon = orm.relationship('Polygon', back_populates='thumbnails', lazy='immediate')

@sql.event.listens_for(Thumbnail, 'after_delete')
def delete_thumbnail(mapper, connection, thumbnail):
    Path(thumbnail.filename).unlink()


class Point(DeclarativeBase):
    __tablename__ = 'point'

    id = sql.Column(sql.Integer, primary_key=True)
    x = sql.Column(sql.Float, nullable=False)
    y = sql.Column(sql.Float, nullable=False)
    polygon_id = sql.Column(sql.Integer, sql.ForeignKey('polygon.id'), nullable=False)
    polygon = orm.relationship('Polygon', back_populates='points', lazy='immediate')

    @property
    def z33n(self):
        x, y, *_ = from_latlon(self.y, self.x, force_zone_number=33, force_zone_letter='N')
        return x, y


class GeoTIFF(DeclarativeBase):
    __tablename__ = 'geotiff'

    id = sql.Column(sql.Integer, primary_key=True)
    filename = sql.Column(sql.String, nullable=False)
    east = sql.Column(sql.Float, nullable=False)
    west = sql.Column(sql.Float, nullable=False)
    south = sql.Column(sql.Float, nullable=False)
    north = sql.Column(sql.Float, nullable=False)

    assocs = orm.relationship(
        'PolyTIFF', back_populates='geotiff', lazy='immediate',
        cascade='save-update, merge, delete, delete-orphan',
    )

    @lru_cache(maxsize=1)
    def dataset(self):
        return gdal.Open(str(self.filename))

    @property
    @lru_cache(maxsize=1)
    def shape(self):
        return self.dataset().ReadAsArray().shape

    @property
    @lru_cache(maxsize=1)
    def resolution(self):
        _, rx, _, _, _, ry = self.dataset().GetGeoTransform()
        return rx, -ry

    def populate(self):
        data = self.dataset()
        nx, ny = self.shape

        # Compute bounding box
        trf = data.GetGeoTransform()
        assert trf[2] == trf[4] == 0
        self.east = trf[0] + 0.5 * trf[1]
        self.west = self.east + trf[1] * (ny - 1)
        self.north = trf[3] + 0.5 * trf[5]
        self.south = self.north + trf[5] * (nx - 1)

    def interpolate(self, data, x, y):
        rx, ry = self.resolution

        # Mask of which indices apply to this TIFF
        I, J = np.where((self.south <= x) & (x < self.north) & (self.east <= y) & (y < self.west))
        x = (self.north - x[I, J]) / rx
        y = (y[I, J] - self.east) / ry

        # Compute indices of the element for each point
        left = np.floor(x).astype(int)
        down = np.floor(y).astype(int)

        # Reference coordinates for each point
        ref_left = x - left
        ref_down = y - down

        # Interpolate
        refdata = self.dataset().ReadAsArray()
        refdata[np.where(refdata < 0)] = 0

        data[I, J] = np.maximum(
            data[I, J],
            refdata[left,   down]   * (1 - ref_left) * (1 - ref_down) +
            refdata[left+1, down]   * ref_left       * (1 - ref_down) +
            refdata[left,   down+1] * (1 - ref_left) * ref_down +
            refdata[left+1, down+1] * ref_left       * ref_down
        )

@sql.event.listens_for(GeoTIFF, 'after_delete')
def delete_geotiff(mapper, connection, geotiff):
    Path(geotiff.filename).unlink()


class PolyTIFF(DeclarativeBase):
    __tablename__ = 'polytiff'

    polygon_id = sql.Column(sql.Integer, sql.ForeignKey('polygon.id'), primary_key=True)
    geotiff_id = sql.Column(sql.Integer, sql.ForeignKey('geotiff.id'), primary_key=True)
    dedicated = sql.Column(sql.Boolean, nullable=False)
    project = sql.Column(sql.String, nullable=False)

    polygon = orm.relationship('Polygon', back_populates='assocs', lazy='immediate')
    geotiff = orm.relationship('GeoTIFF', back_populates='assocs', lazy='immediate')


class Job(DeclarativeBase):
    __tablename__ = 'job'

    id = sql.Column(sql.Integer, primary_key=True)
    polygon_id = sql.Column(sql.Integer, sql.ForeignKey('polygon.id'))
    project = sql.Column(sql.String, nullable=False)
    dedicated = sql.Column(sql.Boolean, nullable=False)
    jobid = sql.Column(sql.Integer, nullable=False)
    stage = sql.Column(sql.String, nullable=False, default='new')
    error = sql.Column(sql.String, nullable=True)
    url = sql.Column(sql.String, nullable=True)

    polygon = orm.relationship('Polygon', back_populates='jobs', lazy='immediate')

    def refresh(self):
        code, response = make_request('exportStatus', {'JobID': self.jobid})
        if response is None:
            self.stage = 'error'
            self.error = f'HTTP code {code}'
        else:
            self.stage = response['Status']
            if self.stage == 'complete':
                self.url = response['Url']
        db.commit()

    def download(self):
        assert self.stage == 'complete'
        assert self.url is not None

        response = requests.get(self.url)
        if response.status_code != 200:
            self.stage = 'error'
            self.error = f'HTTP code {code}'
            return

        with ZipFile(BytesIO(response.content), 'r') as z:
            tifpaths = [path for path in z.namelist() if path.endswith('.tif')]
            if self.dedicated:
                assert len(tifpaths) == 1

            for path in tifpaths:
                data = z.read(path)
                if self.dedicated:
                    filename = hashlib.sha256(data).hexdigest() + '.tiff'
                else:
                    filename = Path(path).stem.split('_', 1)[-1] + '.tiff'
                filename = DATA_ROOT / self.project / filename
                with open(filename, 'wb') as f:
                    f.write(data)
                geotiff = GeoTIFF(filename=str(filename))
                geotiff.populate()
                polytiff = PolyTIFF(polygon=self.polygon, geotiff=geotiff, project=self.project, dedicated=self.dedicated)
                with db.session() as s:
                    s.add(geotiff)
                    s.add(polytiff)

        self.polygon.update_thumbnail(self.project, self.dedicated)
        with db.session() as s:
            s.delete(self)


class Database:

    def __init__(self):
        self.engine = sql.create_engine(f'sqlite:///{DATA_ROOT}/db.sqlite3')
        DeclarativeBase.metadata.create_all(self.engine)
        self._session = orm.sessionmaker(bind=self.engine)()

        self.lfid = bidict()
        self.listeners = []

    @contextmanager
    def session(self):
        session = self._session
        try:
            yield session
            session.commit()
        except:
            session.rollback()
            raise

    def commit(self):
        with self.session() as s:
            pass

    def __iter__(self):
        with self.session() as s:
            yield from s.query(Polygon).order_by(Polygon.name)

    def __len__(self):
        with self.session() as s:
            return s.query(Polygon).count()

    def __getitem__(self, index):
        with self.session() as s:
            return s.query(Polygon).order_by(Polygon.name)[index]

    @contextmanager
    def _job_query(self, stage=None):
        with self.session() as s:
            q = s.query(Job)
            if stage is not None:
                q = q.filter(Job.stage == stage)
            yield q

    def njobs(self, stage=None):
        with self._job_query(stage) as q:
            return q.count()

    def jobs(self, stage=None):
        with self._job_query(stage) as q:
            yield from q.order_by(Job.jobid)

    def delete_if(self, obj):
        if obj is not None:
            with self.session() as s:
                s.delete(obj)

    def poly_by_lfid(self, lfid):
        with self.session() as s:
            return s.query(Polygon).get(self.lfid[lfid])

    def index(self, poly=None, lfid=None):
        if poly is None:
            poly = self.poly_by_lfid(lfid)
        with self.session() as s:
            return s.query(Polygon).filter(Polygon.name < poly.name).count()

    def update_lfid(self, poly, lfid):
        if poly.id in self.lfid.inverse:
            del self.lfid.inverse[poly.id]
        if lfid is not None:
            self.lfid[lfid] = poly.id

    def notify(self, obj):
        self.listeners.append(obj)

    def message(self, method, *args):
        caller = methodcaller(method, *args)
        for listener in self.listeners:
            caller(listener)

    def update_name(self, index, name):
        poly = self[index]

        self.message('before_reset', poly.lfid)
        poly.name = name
        self.commit()
        self.message('after_reset')

    def update_points(self, lfid, data):
        points = json.loads(data)['geometry']['coordinates'][0]
        poly = self.poly_by_lfid(lfid)
        with self.session() as s:
            for point in poly.points:
                s.delete(point)
            for x, y in points:
                s.add(Point(x=x, y=y, polygon=poly))

    def create(self, lfid, name, data):
        points = json.loads(data)['geometry']['coordinates'][0]

        poly = Polygon(name=name)
        for x, y in points:
            Point(x=x, y=y, polygon=poly)
        with self.session() as s:
            s.add(poly)
        poly.lfid = lfid

        index = self.index(poly=poly)
        self.message('before_insert', index)
        self.message('after_insert')

    def delete(self, lfid):
        poly = self.poly_by_lfid(lfid)
        index = self.index(poly=poly)

        self.message('before_delete', index)
        with self.session() as s:
            s.delete(poly)
        self.message('after_delete')


db = Database()
