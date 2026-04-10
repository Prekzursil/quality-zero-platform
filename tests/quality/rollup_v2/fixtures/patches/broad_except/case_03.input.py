try:
    connect()
except BaseException as err:
    cleanup(err)
