"""Authentication package: password hashing, tokens, repositories, and routes.

Submodules: password (argon2id hashing), tokens (opaque session+reset
tokens, stored sha256-hashed), repo (AuthRepo CRUD over the 5 auth
tables), deps (`AuthContext` / `current_user` / `tenant_db` FastAPI
dependencies), email_sender (Dev=stderr / SMTP), ratelimit (in-process
login rate limiter).
"""
