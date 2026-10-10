import os

# An unset APP_ENV counts as production, which refuses the committed dev token (app/config.py).
os.environ["APP_ENV"] = "dev"
