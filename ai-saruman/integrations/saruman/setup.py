"""Setup script for Saruman project.

This makes the project installable in development mode to simplify imports.
"""

from setuptools import setup, find_packages

setup(
    name="saruman-minecraft",
    version="2.0.5",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    py_modules=["main"],
)
