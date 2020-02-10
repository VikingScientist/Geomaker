from io import BytesIO
import json

from humanfriendly import format_size
import numpy as np
import requests
from utm import from_latlon


class SingletonMeta(type):
    """Metaclass for creating singleton classes."""

    __instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls.__instances:
            cls.__instances[cls] = super().__call__(*args, **kwargs)
        return cls.__instances[cls]


def make_request(endpoint, params):
    """Submit a request to hoydedata.no at the given endpoint.
    'Params' should be a dict of parameters.  Returns a tuple with
    HTTP status code and potentially a dict of results.
    """
    params = json.dumps(params)
    url = f'https://hoydedata.no/laserservices/rest/{endpoint}.ashx?request={params}'
    response = requests.get(url)
    if response.status_code != 200:
        return response.status_code, None
    return response.status_code, json.loads(response.text)


def download_streaming(url, mgr):
    response = requests.get(url, stream=True)
    if response.status_code != 200:
        return None
    nbytes = int(response.headers['Content-Length'])
    mgr.report_max(nbytes)
    responsedata = BytesIO()
    down = 0
    for chunk in response.iter_content(16384):
        responsedata.write(chunk)
        down += len(chunk)
        mgr.increment_progress(len(chunk))
        mgr.report_message('Downloading · {}/{}'.format(
            format_size(down, keep_width=True),
            format_size(nbytes, keep_width=True)
        ))
    return responsedata


def convert_latlon(point, coords):
    if coords == 'latlon':
        return point
    elif coords.startswith('utm'):
        zonenum = int(coords[3:-1])
        zoneletter = coords[-1].upper()
        x, y, *_ = from_latlon(point[1], point[0], force_zone_number=33, force_zone_letter='N')
        if isinstance(point[0], np.ndarray):
            return x, y
        return np.array([x, y])
    raise ValueError(f'Unknown coordinate system: {coords}')