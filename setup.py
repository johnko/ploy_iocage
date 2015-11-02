from setuptools import setup
import os


here = os.path.abspath(os.path.dirname(__file__))
README = open(os.path.join(here, 'README.rst')).read()
HISTORY = open(os.path.join(here, 'HISTORY.rst')).read()


version = "0.1.0dev"


setup(
    version=version,
    description="Plugin for ploy to provision FreeBSD jails using iocage.",
    long_description=README + "\n\n" + HISTORY,
    name="ploy_iocage",
    author='Florian Schulze and John Ko',
    author_email='florian.schulze@gmx.net',
    license="BSD 3-Clause License",
    url='http://github.com/johnko/ploy_iocage',
    classifiers=[
        'Environment :: Console',
        'Intended Audience :: System Administrators',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.3',
        'Topic :: System :: Installation/Setup',
        'Topic :: System :: Systems Administration'],
    include_package_data=True,
    zip_safe=False,
    packages=['ploy_iocage'],
    install_requires=[
        'setuptools',
        'ploy >= 1.2.0, < 2dev',
        'lazy'],
    entry_points="""
        [ploy.plugins]
        iocage = ploy_iocage:plugin
    """)
