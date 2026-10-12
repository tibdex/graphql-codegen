import importlib
import pkgutil
import sys

name, *directories = sys.argv[1:]
sys.path[:0] = directories
package = importlib.import_module(name)

# `walk_packages()` ignores a subpackage that fails to import, so each is imported here.
for module in pkgutil.walk_packages(package.__path__, prefix=f"{name}."):
    importlib.import_module(module.name)
