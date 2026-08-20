"""
core/ — Infrastructure layer of the AI Affiliate SaaS platform.

Import order matters:
  1. config      — load settings first (all modules depend on it)
  2. exceptions  — custom exception types (no dependencies)
  3. database    — engine + session factory (depends on config)
  4. redis_client — Redis pool (depends on config)
  5. models/*    — ORM models (depend on database)
"""
