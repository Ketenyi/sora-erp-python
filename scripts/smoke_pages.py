import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client as TestClient
from django.urls import URLPattern, URLResolver, get_resolver

PREFIXES = ("/dashboard", "/sales", "/crm", "/invoices", "/accounts", "/inventory", "/purchasing", "/hr", "/projects", "/helpdesk", "/assets", "/pos", "/settings", "/admin")
SKIP = ("/logout", "/hr/leave/approve", "/hr/leave/reject", "/hr/leave/cancel", "/projects/analytics/reports/save")


def walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern)


client = TestClient(headers={"host": "localhost"})
assert client.login(email="admin@sora.com", password="admin123")

urls = sorted(
    {
        path
        for raw in walk(get_resolver().url_patterns)
        if "<" not in raw
        for path in ["/" + raw]
        if path.startswith(PREFIXES) and not any(s in path for s in SKIP)
    }
)
failures = []
for url in urls:
    response = client.get(url, follow=False)
    if response.status_code >= 400:
        failures.append((url, response.status_code))
        print(f"{response.status_code} {url}")
print(f"checked {len(urls)} urls, {len(failures)} failures")
