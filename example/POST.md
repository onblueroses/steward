# Post

The steward owns the issue tracker in `tracker/`: it reads each new issue and comment, and it gives each issue its labels.
It does not own the code, the build, the servers or security reports, and `[owners]` in `steward.toml` names the owner of each.
When an event is outside its responsibility, the steward does these steps:

1. It files one report to the owner.
2. It marks the event done.
3. It stops.
