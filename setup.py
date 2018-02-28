from setuptools import setup

setup(
    name = "flexbackup-manager",
    version = "0.0.1",
    author = "Chen Yun-Chih",
    author_email = "yunchih@csie.ntu.edu.tw",
    description = ("Flexbackup scheduling manager"),
    license = "MIT",
    keywords = "flexbackup backup",
    url = "http://packages.python.org/an_example_pypi_project",
    scripts = ['flexbackup-manager'],
    long_description='''
        This script is a backup scheduler built upon flexbackup
        that manages backup set according to their tiers and
        corresponding SLAs.  We currently support two tiers with
        different SLAs.
    ''',
)
