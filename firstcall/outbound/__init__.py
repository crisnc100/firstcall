"""The only code that sends anything out of Firstcall.

Every send passes the recipient allowlist first: email only to active users of the org and
never to a buyer's address, push only to devices users subscribed, on a known push service
host. A test fails if any module outside this package imports a mail or push library or a
sending HTTP client.
"""
