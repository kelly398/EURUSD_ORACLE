import os
from dotenv import load_dotenv

load_dotenv()


OANDA_API_KEY = os.getenv(
    "OANDA_API_KEY"
)

OANDA_ACCOUNT_ID = os.getenv(
    "OANDA_ACCOUNT_ID"
)

DATABASE_URL = os.getenv(
    "DATABASE_URL"
)