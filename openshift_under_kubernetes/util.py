def deepupdate(original, update):
    """
    Recursively update a dict.
    """
    for key, value in original.iteritems():
        if key not in update:
            update[key] = value
        elif isinstance(value, dict):
            deepupdate(value, update[key])
    return update
