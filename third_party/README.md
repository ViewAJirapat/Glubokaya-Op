# third_party

## Aider.zip

An unmodified source snapshot of [Aider](https://github.com/Aider-AI/aider), the AI pair-programming
tool that `local-ai/aider-local` and the `swarm` harness's `aider` engines drive. It's kept here as a
backup of the exact version this project was built and tested against.

| | |
|---|---|
| Version | v0.86.2 (same as the installed `aider-chat`) |
| Source | https://codeload.github.com/Aider-AI/aider/zip/refs/tags/v0.86.2 |
| Commit | `253f0368b873ba30d8ee26e463718f0c03614ddf` (stored in the zip's comment) |
| SHA-256 | `2769b32882ec6dec7cc31c3af991e7de3660062d10ac1c149e3fdb4766ee122b` |
| Size | 67.8 MB zipped, 77.7 MB unzipped, 774 entries, top folder `aider-0.86.2/` |
| License | Apache-2.0, see `aider-0.86.2/LICENSE.txt` inside the zip |
| Downloaded | 2026-10-21 |

Aider is copyright its authors (Paul Gauthier and contributors) and is redistributed here unmodified
under the Apache License 2.0.

**To reinstall Aider**, the simplest route is still the published package at the same version:

```
uv tool install --python 3.12 aider-chat==0.86.2
```

Use the zip when that isn't available (e.g. offline, or the package is gone): unzip it and read
`aider-0.86.2/README.md` for building from source.

**Check the file** after cloning (PowerShell):

```
Get-FileHash third_party\Aider.zip -Algorithm SHA256
```

The hash must match the SHA-256 above.
