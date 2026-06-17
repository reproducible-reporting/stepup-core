Steps that require a resource whose available capacity is explicitly set to zero.
Setting STEPUP_RESOURCES=token:0 means no step requiring the token resource can ever run.
The build finishes with the affected steps in the PENDING state.
