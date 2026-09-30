"""Load the test-database guard before test modules import the application."""

pytest_plugins = ["tests.db_guard"]
