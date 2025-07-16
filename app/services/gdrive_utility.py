import requests

def is_drive_public(url: str) -> bool:
    try:
        resp = requests.get(url, allow_redirects=True, timeout=10)
        # If we are redirected to a Google login page, it's private
        return 'ServiceLogin' not in resp.url
    except requests.RequestException:
        return False