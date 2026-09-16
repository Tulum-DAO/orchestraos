# Extraction notes

This repo was assembled from a private operator's live install by an
automated extraction pass. The full extraction report (what was included,
excluded-with-reason, literal counts, scanner output, test results) is kept
privately rather than in this repo, since it necessarily quotes some of the
patterns it was scrubbing.

Literal classes that were swept to zero and replaced with `orchestra.toml`
config reads or environment variables before this repo was published:

- **Home/user paths** (e.g. a specific operator's home directory, both Linux
  and macOS forms)
- **Tailnet hostnames** (Tailscale MagicDNS names)
- **Machine IPs** (Tailscale CGNAT-range addresses for specific machines)
- **Chat IDs** (Telegram numeric chat identifiers)
- **Bot tokens** (Telegram bot API tokens — matched both a specific known
  value and the general `<digits>:AA<base64>` shape)
- **A specific operator's name/username**, in code comments, example paths,
  and prose — genericized to "the operator" or a config value

A secret scanner (detect-secrets) ran against the full tree before the
initial commit; see `.secrets.baseline` for the (all false-positive) findings
it tracks.
