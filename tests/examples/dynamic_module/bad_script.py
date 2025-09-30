#!/usr/bin/env python3

import sys
import types


class DynModule(types.ModuleType):
    __file__ = "_dyn_module.py"


dyn_module = DynModule("_dyn_module")
sys.modules["_dyn_module"] = dyn_module
