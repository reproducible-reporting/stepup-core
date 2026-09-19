from shutil import copy

from stepup.core.api import amend

amend(inp="data.txt", out=["copy2.txt", "../${HERE}/copy4.txt"])
copy("data.txt", "copy2.txt")
copy("data.txt", "copy4.txt")
