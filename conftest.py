"""Root conftest.

Its presence at the project root makes pytest prepend the project root to
``sys.path``, so tests can ``import src.<module>`` without an installed package.
"""
