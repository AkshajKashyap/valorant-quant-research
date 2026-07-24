# Raw data snapshots

Raw source files are immutable after acquisition. A snapshot lives under a
source/version directory and contains the downloaded archive, extracted files,
and a `MANIFEST.json` with source metadata and SHA-256 hashes.

The archive and extracted data are intentionally ignored by Git. Do not edit,
rename, clean, or overwrite files inside a snapshot; create a new snapshot for
a new source version instead.
