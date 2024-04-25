When using named glob with more than one pattern, only combinations of consistent matches of those patterns will result in an actual match for the total named glob.

This example pastes files `head_${*char}.txt` and `tail_${*char}.txt`,
where the placeholder `${*char}` must be the same single letter.
