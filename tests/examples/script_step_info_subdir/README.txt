The step info file of a script is written in a directory other than the working directory.
A relative workdir in a step info file is relative to the directory that contains the file,
so the workdir of the step, work/, is written as ../work in data/step_info.json.
main.sh loads the file from the top level and from work/,
and both give the same step info, whose workdir is relative to the current directory.
