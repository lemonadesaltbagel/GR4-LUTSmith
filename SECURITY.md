# Security

This tool writes firmware for a camera, so two kinds of flaw would matter:

- a crafted `look.json` or `.cube` file that gets past the tool's checks and
  ends up as firmware a camera should not receive — one that damages the
  camera or touches what it should not;
- a way to make the tool or the app run code from such a file. Both read these
  files as data and never execute them, but if you find a path that does, that
  is a security problem.

The general risks of putting firmware on a camera — an interrupted update, a
file from someone else — are not security vulnerabilities; they are described
in the README under [Risks](README.md#risks) and
[Check it came from this project](README.md#check-it-came-from-this-project-optional).

Please report privately, so a fix is out before such a file can go around:
use **Report a vulnerability** on this repository's **Security** tab. A release
with the fix goes out as soon as it is ready, with credit if you want it.
