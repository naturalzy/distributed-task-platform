import os

# The test key is intentionally local to the test process.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-with-at-least-32-characters")

