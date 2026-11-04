import os

# The background provider health monitor would ping the real (unconfigured)
# provider sites from every TestClient(app) lifespan and flip the Torznab
# endpoint to 503. Must be set before app.config builds its Settings.
os.environ.setdefault("HEALTH_CHECK_INTERVAL", "0")
