"""Control-plane ports: replaceable infrastructure contracts.

Application code depends on these protocols. Adapters live beside the current
provider (PostgreSQL repositories, Redis rate limiter, MQTT broker via the data plane).
"""
