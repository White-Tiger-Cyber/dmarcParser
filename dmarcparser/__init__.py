# dmarcparser/__init__.py
__version__ = "0.1.0"

# pyproject.toml (ensure these)
[project]
name = "dmarcparser"
version = "0.1.0"
requires-python = ">=3.9"
# ...
[project.scripts]
dP = "dmarcparser.cli:main"
