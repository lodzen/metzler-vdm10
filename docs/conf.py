import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

project = "vdm10-python"
copyright = "2026, lodzen"
author = "lodzen"
try:
    from importlib.metadata import version as _v

    version = _v("vdm10-python")
except Exception:
    version = "0.1.0"
release = version

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.githubpages",
    "myst_parser",
    "sphinxcontrib.mermaid",
    "sphinx_rtd_theme",
]

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

# docs/ is the Sphinx source root so the existing markdown tree stays put
exclude_patterns = [
    "diagrams",
    "build",
    "_build",
    "api/.gitignore",
]

myst_fence_as_directive = ["mermaid"]

html_theme = "sphinx_rtd_theme"
html_logo = "logo.png"
html_theme_options = {
    "logo_only": True,
}
