import importlib
import sys

directory, dependencies, *names = sys.argv[1:]
sys.path[:0] = [directory, dependencies]

for name in names:
    importlib.import_module(name)
