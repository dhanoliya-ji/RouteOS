"""The vehicle-movement clock.

One asyncio loop advances every active vehicle, writes the result to the
database and broadcasts it. The backend owns movement; the frontend only
renders it. See README.md.
"""
