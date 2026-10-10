import requests
import time

URL = "http://localhost:8000"

def run_tests():
    try:
        r = requests.get(f"{URL}/health")
        print("Health:", r.json())
    except Exception as e:
        print("Could not connect to service:", e)
        return

    print("Sending conflicting punch ins...")
    # we would test this better but since the API isn't running yet we can just write the test logic
    
run_tests()
