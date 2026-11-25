# Signals that the catalog projection cannot be read with a usable checkpoint.
class ProjectionUnavailable(Exception):
    pass


# Signals that the requested listing is absent from the queried marketplace.
class ListingNotFound(Exception):
    pass
