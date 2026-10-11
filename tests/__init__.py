import os

# Settings refuse to load without a SECRET_KEY; tests never log in and never call Gemini.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
os.environ["GEMINI_API_KEY"] = ""
