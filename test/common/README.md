# Shared utilities

`provenance.py` captures source hashes, interpreter/dependency versions and
available Test 13 build artifacts. Keep shared helpers stable; changes can
affect multiple experiments. Protocol and measurement code stays with its
numbered experiment, and dependencies use explicit package imports.
