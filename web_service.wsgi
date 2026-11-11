import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from web_service import app as application
from web_service import init_db, wait_for_db

wait_for_db()
init_db()
