def load_data(path):
    try:
        return parse(path)
    except Exception as e:
        log.warning("failed: %s", e)
        return None
