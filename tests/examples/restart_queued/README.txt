It may happen that StepUp exists while some steps are in the QUEUED or RUNNING state.
When StepUp restarts with such a database, these are treated as FAILED
and made PENDING again.
