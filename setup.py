from setuptools import find_packages, setup

setup(
    name="d11",
    version="2.0.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    entry_points={
        "console_scripts": [
            "d11 = d11.cli:main",
        ],
    },
)
