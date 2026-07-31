Three overlapping glob() patterns all match data/a.txt: two from the main plan
("data/*.txt" and "data/a.txt"), and one from a completely different plan
(sub/plan.py, via "../data/*.txt"). Since a glob pattern is a pure query that owns
nothing, any number of patterns may match the same static file, from any number of
plans, without conflict.
