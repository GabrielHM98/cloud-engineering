#!/usr/bin/env python3
"""Run the app suite with its frozen engine modules on the import path."""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    os.chdir(ROOT)
    sys.path[:0]=[str(ROOT/'engine'),str(ROOT)]
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
