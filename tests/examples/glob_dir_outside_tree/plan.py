#!/usr/bin/env python3
from stepup.core.api import glob

# No static tree declared: this directory match must raise, since StepUp has no
# evidence that sub/leaf/ is source material rather than a step's build product.
list(glob("sub/*/"))
