Verify that --preload-modules works with the forkserver.

The stepup.toml configures forkserver=true and preloads numpy and matplotlib.
The work.py script asserts that these modules are already in sys.modules
when it runs (i.e. inherited from the forkserver without re-importing),
which would only be true if preloading worked correctly.
