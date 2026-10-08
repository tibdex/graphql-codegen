"""Import every module of a package, so that any module failing to import fails the script.

    python import_every_module.py <package> <directory>...

The directories are searched before any other, for the package and for everything it imports.
"""

import importlib
import pkgutil
import sys

name, *directories = sys.argv[1:]
sys.path[:0] = directories
package = importlib.import_module(name)

# A subpackage failing to import is yielded before `walk_packages()` ignores it, so importing it here raises.
for module in pkgutil.walk_packages(package.__path__, prefix=f"{name}."):
    importlib.import_module(module.name)
