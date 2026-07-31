static(ng), where ng is a NamedGlob returned by glob(), is now the sole declaration of
its matches: glob() itself declares nothing. static(ng) must not register the pattern a
second time -- glob() already did that when it produced ng.
