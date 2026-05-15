import sys
from pathlib import Path
import os
import django

sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

project = "Task Dashboard"
author = "Justus Jäger"
copyright = "2026, ngenn GmbH"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "pydata_sphinx_theme"
html_static_path = ["_static"]

html_theme_options = {
    "show_toc_level": 2,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/nGENn/task-dashboard",
            "icon": "fa-brands fa-github",
        }
    ],
}
