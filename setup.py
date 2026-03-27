from setuptools import setup, find_packages

setup(
    name="projectstack",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.1",
    ],
    entry_points={
        "console_scripts": [
            "pstack=projectstack.cli.main:cli",
        ],
    },
)
