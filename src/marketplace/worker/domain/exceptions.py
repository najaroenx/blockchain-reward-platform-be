# Signals that the current Worker can no longer modify the job under its lease.
class LeaseLost(Exception):
    pass
