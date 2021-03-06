#!/usr/bin/env python3

from distutils.core import setup
from setuptools import find_packages

setup(
    name='GeoMaker',
    version='0.1',
    description='Height data extraction tool',
    author='Eivind Fonn',
    author_email='evfonn@gmail.com',
    license='GPL3',
    url='https://github.com/TheBB/geomaker',
    packages=find_packages(include=['geomaker']),
    package_data={
        'geomaker': ['assets/map.html', 'assets/map.js'],
    },
    entry_points={
        'console_scripts': ['geomaker=geomaker.__main__:main'],
    },
    install_requires=[
        'area',
        'bidict',
        'GDAL',
        'matplotlib',
        'PyQt5',
        'PyQtWebEngine',
        'requests',
        'SQLAlchemy',
        'toml',
        'utm',
        'xdg',
    ],
)
