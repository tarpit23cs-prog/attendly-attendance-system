"""
config.py
---------
Loads settings from the .env file so we never hardcode
secrets or database passwords directly in the code.
"""

import os
from dotenv import load_dotenv

# Load variables from the .env file into the environment
load_dotenv()

# Flask secret key (used for sessions/cookies)
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")

# MySQL database settings
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "attendly")

# Office timing rules (used for late/present calculation)
OFFICE_START_TIME = "09:30:00"   # official start time
GRACE_PERIOD_MINUTES = 15        # employee can clock in until 09:45 and still be "Present"
